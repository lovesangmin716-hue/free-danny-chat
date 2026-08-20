from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


ROOT = Path(__file__).parents[1]
SRC_DIR = ROOT / "src"


def request(port: int, method: str, path: str, payload: dict | None = None, cookie: str = "") -> tuple[int, dict, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read()
        data = json.loads(content) if content else {}
        set_cookie = response.getheader("Set-Cookie", "").split(";", 1)[0]
        return response.status, data, set_cookie
    finally:
        connection.close()


def wait_ready(port: int, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _, _ = request(port, "GET", "/health")
            if status == 200:
                return
        except OSError:
            pass
        time.sleep(0.05)
    raise RuntimeError(f"server on port {port} did not become ready")


def start_server(port: int, state_file: Path, instance_id: str) -> subprocess.Popen:
    environment = os.environ.copy()
    environment.update(
        {
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "DATA_DIR": str(state_file.parent),
            "STATE_FILE": str(state_file),
            "INSTANCE_ID": instance_id,
            "EVENT_POLL_INTERVAL_SECONDS": "0.05",
            "PRESENCE_TTL_SECONDS": "15",
            "SOCIAL_DEMO_LOGIN_ENABLED": "false",
        }
    )
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(SRC_DIR), environment.get("PYTHONPATH", "")) if part
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "colorless"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_ready(port)
    return process


def stop_server(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def signup(port: int, username: str, friend_code: str, phone: str) -> str:
    status, code, _ = request(port, "POST", "/phone/request-code", {"phone": phone})
    if status != 200:
        raise RuntimeError(code)
    status, verified, _ = request(
        port,
        "POST",
        "/phone/verify-code",
        {"phone": phone, "code": code["devCode"]},
    )
    if status != 200:
        raise RuntimeError(verified)
    status, created, cookie = request(
        port,
        "POST",
        "/signup",
        {
            "username": username,
            "friendCode": friend_code,
            "password": "integration-password",
            "phone": phone,
            "verificationToken": verified["verificationToken"],
            "ageGroup": "20대",
            "gender": "여성",
        },
    )
    if status != 201:
        raise RuntimeError(created)
    return cookie


class SseReader:
    def __init__(self, port: int, cookie: str, after: int = 0) -> None:
        self.events: queue.Queue[dict] = queue.Queue()
        self.connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        path = f"/events?after={after}" if after else "/events"
        self.connection.request("GET", path, headers={"Cookie": cookie})
        self.response = self.connection.getresponse()
        if self.response.status != 200:
            raise RuntimeError(f"SSE failed with HTTP {self.response.status}")
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        event_id = ""
        while True:
            try:
                line = self.response.fp.readline()
            except OSError:
                return
            if not line:
                return
            decoded = line.decode("utf-8").strip()
            if decoded.startswith("id:"):
                event_id = decoded.removeprefix("id:").strip()
            elif decoded.startswith("data:"):
                event = json.loads(decoded.removeprefix("data:").strip())
                if event_id:
                    event["sse_id"] = int(event_id)
                self.events.put(event)
                event_id = ""

    def wait_for(self, event_type: str, timeout: float = 5) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if event.get("type") == event_type:
                return event
        raise RuntimeError(f"did not receive {event_type}")

    def close(self) -> None:
        self.connection.close()
        self.thread.join(timeout=1)


def run_probe(port_a: int, port_b: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="colorless-multi-instance-") as temp_dir:
        state_file = Path(temp_dir) / "state.json"
        server_a = start_server(port_a, state_file, "integration-a")
        server_b = start_server(port_b, state_file, "integration-b")
        alice_sse = None
        bob_sse = None
        replacement_sse = None
        try:
            alice_cookie = signup(port_a, "multialice", "multi_alice", "01010000001")
            bob_cookie = signup(port_b, "multibob", "multi_bob", "01010000002")

            stop_server(server_a)
            stop_server(server_b)
            server_a = start_server(port_a, state_file, "integration-a2")
            server_b = start_server(port_b, state_file, "integration-b2")

            status, friend, _ = request(
                port_a, "POST", "/friends", {"friendCode": "multi_bob"}, alice_cookie
            )
            if status != 201:
                raise RuntimeError(friend)
            status, room_data, _ = request(
                port_a, "POST", "/direct-rooms", {"userId": friend["friend"]["id"]}, alice_cookie
            )
            if status not in {200, 201}:
                raise RuntimeError(room_data)
            room_id = room_data["room"]["id"]

            alice_sse = SseReader(port_a, alice_cookie)
            bob_sse = SseReader(port_b, bob_cookie)
            alice_sse.wait_for("hello")
            bob_sse.wait_for("hello")

            started = time.perf_counter()
            status, first_message, _ = request(
                port_a,
                "POST",
                "/messages",
                {"roomId": room_id, "text": "cross-instance", "clientMessageId": "integration-msg-0001"},
                alice_cookie,
            )
            if status != 201:
                raise RuntimeError(first_message)
            delivered = bob_sse.wait_for("message_created")
            delivery_ms = (time.perf_counter() - started) * 1000
            if delivered["message"]["id"] != first_message["id"]:
                raise RuntimeError("cross-instance event referenced the wrong message")

            status, retried, _ = request(
                port_a,
                "POST",
                "/messages",
                {"roomId": room_id, "text": "cross-instance", "clientMessageId": "integration-msg-0001"},
                alice_cookie,
            )
            if status != 200 or retried["id"] != first_message["id"]:
                raise RuntimeError("client message retry was not idempotent")

            first_revision = int(delivered["revision"])
            alice_sse.close()
            alice_sse = None
            stop_server(server_a)
            status, second_message, _ = request(
                port_b,
                "POST",
                "/messages",
                {"roomId": room_id, "text": "during-rollout", "clientMessageId": "integration-msg-0002"},
                bob_cookie,
            )
            if status != 201:
                raise RuntimeError(second_message)

            server_a = start_server(port_a, state_file, "integration-a3")
            replacement_sse = SseReader(port_a, alice_cookie, first_revision)
            replacement_sse.wait_for("hello")
            replayed = replacement_sse.wait_for("message_created")
            if replayed["message"]["id"] != second_message["id"]:
                raise RuntimeError("rolling replay did not recover the missed message")

            status, metrics, _ = request(port_b, "GET", "/metrics")
            if status != 200:
                raise RuntimeError(metrics)
            if delivery_ms >= 1000:
                raise RuntimeError(f"event delivery exceeded 1 second: {delivery_ms:.3f} ms")
            return {
                "delivery_ms": round(delivery_ms, 3),
                "first_revision": first_revision,
                "replayed_revision": replayed["revision"],
                "idempotent_message_id": first_message["id"],
                "metrics": metrics["sse"],
            }
        finally:
            for reader in (alice_sse, bob_sse, replacement_sse):
                if reader is not None:
                    reader.close()
            stop_server(server_a)
            stop_server(server_b)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run two Colorless instances against one database.")
    parser.add_argument("--port-a", type=int, default=8875)
    parser.add_argument("--port-b", type=int, default=8876)
    args = parser.parse_args()
    print(json.dumps(run_probe(args.port_a, args.port_b), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
