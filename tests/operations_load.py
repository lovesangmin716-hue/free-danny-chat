from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import math
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


APP_DIR = Path(__file__).parents[1] / "outputs" / "chat-app"
SERVER_PATH = APP_DIR / "server.py"
PDF_FIXTURE = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"

PROFILES = {
    "smoke": {"concurrency": 1, "requests": 24, "sse": 4},
    "load": {"concurrency": 12, "requests": 300, "sse": 20},
    "spike": {"concurrency": 32, "requests": 400, "sse": 32},
    "soak": {"concurrency": 12, "requests": 6_000, "sse": 20},
}

SLO = {
    "message_p95_ms": 300.0,
    "read_p95_ms": 500.0,
    "realtime_p95_ms": 1_000.0,
    "server_error_rate": 0.001,
}


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def latency_summary(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "p99_ms": round(percentile(values, 0.99), 3),
        "max_ms": round(max(values, default=0.0), 3),
    }


@dataclass
class Response:
    status: int
    payload: Any
    headers: dict[str, str]
    latency_ms: float


class ApiClient:
    def __init__(self, base_url: str, cookie: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 10,
    ) -> Response:
        request_body = body
        request_headers = {"Accept": "application/json", **(headers or {})}
        if payload is not None:
            request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if self.cookie:
            request_headers["Cookie"] = self.cookie
        request = Request(
            f"{self.base_url}{path}",
            data=request_body,
            headers=request_headers,
            method=method,
        )
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
                status = response.status
                response_headers = dict(response.headers)
        except HTTPError as error:
            raw = error.read()
            status = error.code
            response_headers = dict(error.headers)
        latency_ms = (time.perf_counter() - started) * 1_000
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = raw
        return Response(status, decoded, response_headers, latency_ms)

    def login(self, username: str, password: str) -> Response:
        response = self.request(
            "/login",
            "POST",
            {"username": username, "password": password},
        )
        cookies = SimpleCookie()
        cookies.load(response.headers.get("Set-Cookie", ""))
        session = cookies.get("codex_talk_session")
        if response.status != 200 or session is None:
            raise RuntimeError(f"login failed with HTTP {response.status}: {response.payload}")
        self.cookie = f"codex_talk_session={session.value}"
        return response


class FixtureServer(AbstractContextManager):
    def __init__(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="colorless-operations-")
        self.server_module = None
        self.http_server = None
        self.server_thread = None
        self.username = "operations-user"
        self.password = "operations-password"
        self.room_id = ""
        self.base_url = ""

    def __enter__(self) -> "FixtureServer":
        data_dir = Path(self.temp_dir.name)
        os.environ.update({
            "DATA_DIR": str(data_dir),
            "STATE_FILE": str(data_dir / "state.json"),
            "UPLOADS_DIR": str(data_dir / "uploads"),
            "STRUCTURED_LOGS_ENABLED": "false",
            "YOUTUBE_API_KEY": "",
        })
        spec = importlib.util.spec_from_file_location(
            f"colorless_operations_{time.time_ns()}",
            SERVER_PATH,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("server module could not be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.server_module = module

        user, error = module.STORE.create_local_user(
            self.username,
            "operations_user",
            self.password,
            "",
            "01012345678",
            "20대",
            "남성",
        )
        if error or user is None:
            raise RuntimeError(error or "fixture user could not be created")
        peer, error = module.STORE.create_local_user(
            "operations-peer",
            "operations_peer",
            self.password,
            "",
            "01087654321",
            "20대",
            "여성",
        )
        if error or peer is None:
            raise RuntimeError(error or "fixture peer could not be created")
        _, error = module.STORE.add_friend_by_code(self.username, peer["friend_code"])
        room, _, error = module.STORE.create_or_get_direct_room(self.username, peer["id"])
        if error or room is None:
            raise RuntimeError(error or "fixture room could not be created")
        self.room_id = room["id"]

        self.http_server = module.ChatServer(("127.0.0.1", 0), module.ChatHandler)
        self.server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.http_server.server_address[1]}"
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.http_server is not None:
            self.http_server.shutdown()
            self.http_server.server_close()
        if self.server_thread is not None:
            self.server_thread.join(timeout=5)
        if self.server_module is not None:
            self.server_module.SHORTS_COLLECTOR.close()
            self.server_module.EVENT_BROKER.close()
            self.server_module.STORE.close()
        self.temp_dir.cleanup()


def run_upload(client: ApiClient) -> dict:
    grant = client.request(
        "/uploads/grant",
        "POST",
        {"name": "operations.pdf", "type": "application/pdf", "size": len(PDF_FIXTURE)},
    )
    if grant.status != 201:
        return {"ok": False, "stage": "grant", "status": grant.status}
    upload = grant.payload["upload"]
    transfer = client.request(
        upload["url"],
        upload["method"],
        body=PDF_FIXTURE,
        headers=upload["headers"],
    )
    if transfer.status != 200:
        return {"ok": False, "stage": "transfer", "status": transfer.status}
    complete = client.request("/uploads/complete", "POST", {"id": upload["id"]})
    return {
        "ok": complete.status == 201,
        "stage": "complete",
        "status": complete.status,
        "latency_ms": round(grant.latency_ms + transfer.latency_ms + complete.latency_ms, 3),
    }


def run_realtime_probe(
    base_url: str,
    cookie: str,
    room_id: str,
    client: ApiClient,
    connections: int,
) -> dict:
    parsed = urlparse(base_url)
    ready = threading.Condition()
    started_count = 0
    deliveries_ms: list[float] = []
    errors: list[str] = []
    message_sent_at = [0.0]

    def stream() -> None:
        nonlocal started_count
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=15)
        try:
            connection.request("GET", "/events", headers={"Cookie": cookie, "Accept": "text/event-stream"})
            response = connection.getresponse()
            if response.status != 200:
                errors.append(f"SSE HTTP {response.status}")
                return
            while True:
                line = response.readline()
                if not line:
                    raise RuntimeError("SSE closed before hello")
                if b'"type": "hello"' in line:
                    break
            with ready:
                started_count += 1
                ready.notify_all()
            while True:
                line = response.readline()
                if not line:
                    raise RuntimeError("SSE closed before message event")
                if b'"type": "message_created"' in line:
                    deliveries_ms.append((time.perf_counter() - message_sent_at[0]) * 1_000)
                    return
        except Exception as error:  # noqa: BLE001 - the probe reports transport failures.
            errors.append(str(error))
        finally:
            connection.close()

    threads = [threading.Thread(target=stream, daemon=True) for _ in range(connections)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 20
    with ready:
        while started_count + len(errors) < connections and time.monotonic() < deadline:
            ready.wait(timeout=0.2)

    message_sent_at[0] = time.perf_counter()
    message = client.request(
        "/messages",
        "POST",
        {
            "roomId": room_id,
            "text": "operations realtime probe",
            "clientMessageId": f"ops_realtime_{time.time_ns()}",
        },
    )
    for thread in threads:
        thread.join(timeout=15)
    return {
        "requested_connections": connections,
        "accepted_connections": started_count,
        "delivered_connections": len(deliveries_ms),
        "message_status": message.status,
        "delivery": latency_summary(deliveries_ms),
        "errors": errors,
    }


def run_dependency_failure_probe(client: ApiClient, server_module) -> dict:
    if server_module is None:
        return {"tested": False, "reason": "external target is read-only"}
    repository = server_module.STORE.repository
    original = repository.is_legacy_imported

    def unavailable() -> bool:
        raise ConnectionError("injected database outage")

    repository.is_legacy_imported = unavailable
    try:
        live = client.request("/live")
        response = client.request("/ready")
    finally:
        repository.is_legacy_imported = original
    return {
        "tested": True,
        "live_status": live.status,
        "status": response.status,
        "ready": bool(response.payload.get("ready")) if isinstance(response.payload, dict) else None,
        "database_check": response.payload.get("checks", {}).get("database") if isinstance(response.payload, dict) else None,
        "passed": live.status == 200 and response.status == 503 and response.payload.get("checks", {}).get("database") is False,
    }


def run_profile(
    base_url: str,
    username: str,
    password: str,
    room_id: str,
    profile_name: str,
    server_module=None,
    profile_overrides: dict[str, int | float] | None = None,
) -> dict:
    profile = {**PROFILES[profile_name], **(profile_overrides or {})}
    client = ApiClient(base_url)
    login = client.login(username, password)
    live = client.request("/live")
    ready = client.request("/ready")
    upload = run_upload(client)
    realtime_client = ApiClient(base_url)
    realtime_client.login(username, password)
    realtime = run_realtime_probe(
        base_url,
        realtime_client.cookie,
        room_id,
        client,
        int(profile["sse"]),
    )
    realtime_client.request("/logout", "POST", {})
    client.request(
        "/messages",
        "POST",
        {
            "roomId": room_id,
            "text": "operations realtime cleanup",
            "clientMessageId": f"ops_realtime_cleanup_{time.time_ns()}",
        },
    )
    cleanup_deadline = time.monotonic() + 5
    while time.monotonic() < cleanup_deadline:
        cleanup_metrics = client.request("/metrics")
        if int(cleanup_metrics.payload.get("sse", {}).get("active", 0)) == 0:
            break
        time.sleep(0.05)

    route_latencies: dict[str, list[float]] = {"message": [], "read": [], "shorts": []}
    statuses: list[int] = []
    lock = threading.Lock()

    def exercise(index: int) -> tuple[str, Response]:
        request_client = ApiClient(base_url, client.cookie)
        operation = index % 4
        if index < 800 and index % 20 == 4:
            route, response = "shorts", request_client.request("/youtube/shorts")
        elif operation == 0:
            route = "message"
            response = request_client.request(
                "/messages",
                "POST",
                {
                    "roomId": room_id,
                    "text": f"operations load message {index}",
                    "clientMessageId": f"ops_load_{profile_name}_{time.time_ns()}_{index}",
                },
            )
        elif operation == 1:
            route, response = "read", request_client.request("/messenger")
        elif operation == 2:
            route, response = "read", request_client.request("/rooms?limit=30")
        elif operation == 3:
            route, response = "read", request_client.request("/messages?room_id=" + room_id)
        with lock:
            route_latencies[route].append(response.latency_ms)
            statuses.append(response.status)
        return route, response

    started = time.perf_counter()
    exceptions: list[str] = []
    requested_duration_seconds = float(profile.get("duration_seconds", 0))
    with ThreadPoolExecutor(max_workers=int(profile["concurrency"])) as executor:
        if requested_duration_seconds > 0:
            deadline = time.monotonic() + requested_duration_seconds
            counter = iter(range(2_147_483_647))
            counter_lock = threading.Lock()

            def exercise_until_deadline() -> None:
                while time.monotonic() < deadline:
                    with counter_lock:
                        index = next(counter)
                    try:
                        exercise(index)
                    except Exception as error:  # noqa: BLE001 - the report retains load failures.
                        with lock:
                            exceptions.append(str(error))

            futures = [
                executor.submit(exercise_until_deadline)
                for _ in range(int(profile["concurrency"]))
            ]
        else:
            futures = [executor.submit(exercise, index) for index in range(int(profile["requests"]))]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as error:  # noqa: BLE001 - a load failure belongs in the report.
                exceptions.append(str(error))
    duration_seconds = time.perf_counter() - started

    metrics = client.request("/metrics")
    dependency_failure = run_dependency_failure_probe(client, server_module)
    total = len(statuses) + len(exceptions)
    server_errors = sum(status >= 500 for status in statuses) + len(exceptions)
    report = {
        "profile": profile_name,
        "target": {
            "base_url": base_url,
            "concurrency": profile["concurrency"],
            "request_budget": profile["requests"],
            "duration_budget_seconds": requested_duration_seconds,
            "completed_requests": len(statuses),
            "sse_connections": profile["sse"],
        },
        "duration_seconds": round(duration_seconds, 3),
        "throughput_rps": round(len(statuses) / duration_seconds, 3) if duration_seconds else 0,
        "probes": {
            "login": login.status,
            "live": live.status,
            "ready": ready.status,
            "upload": upload,
            "realtime": realtime,
            "dependency_failure": dependency_failure,
        },
        "latency": {name: latency_summary(values) for name, values in route_latencies.items()},
        "responses": {
            "total": total,
            "2xx": sum(200 <= status < 300 for status in statuses),
            "4xx": sum(400 <= status < 500 for status in statuses),
            "5xx": server_errors,
            "server_error_rate": round(server_errors / max(1, total), 6),
            "exceptions": exceptions,
        },
        "server_metrics": metrics.payload,
        "slo": SLO,
    }
    queue_drops = int(metrics.payload.get("sse", {}).get("queue_drops_total", 0))
    checks = {
        "liveness": live.status == 200,
        "readiness": ready.status == 200 and bool(ready.payload.get("ready")),
        "upload": bool(upload["ok"]),
        "all_sse_accepted": realtime["accepted_connections"] == profile["sse"],
        "all_sse_delivered": realtime["delivered_connections"] == profile["sse"],
        "realtime_p95": realtime["delivery"]["p95_ms"] <= SLO["realtime_p95_ms"],
        "message_p95": latency_summary(route_latencies["message"])["p95_ms"] <= SLO["message_p95_ms"],
        "read_p95": latency_summary(route_latencies["read"])["p95_ms"] <= SLO["read_p95_ms"],
        "server_error_rate": report["responses"]["server_error_rate"] <= SLO["server_error_rate"],
        "no_unexpected_client_errors": report["responses"]["4xx"] == 0,
        "no_sse_queue_drops": queue_drops == 0,
        "dependency_failure": dependency_failure.get("passed", True),
    }
    report["checks"] = checks
    report["passed"] = all(checks.values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run login, messaging, SSE, upload, Shorts and dependency-failure operations scenarios."
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), default="smoke")
    parser.add_argument("--base-url", help="Existing HTTP target. Omit to start an isolated local fixture server.")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--room-id")
    parser.add_argument("--requests", type=int, help="Override the profile request count.")
    parser.add_argument("--concurrency", type=int, help="Override concurrent API workers.")
    parser.add_argument("--sse-connections", type=int, help="Override concurrent SSE streams.")
    parser.add_argument("--duration-seconds", type=float, help="Run API workers for this duration instead of a request count.")
    args = parser.parse_args()
    overrides = {
        key: value
        for key, value in {
            "requests": args.requests,
            "concurrency": args.concurrency,
            "sse": args.sse_connections,
            "duration_seconds": args.duration_seconds,
        }.items()
        if value is not None
    }
    if any(float(value) <= 0 for value in overrides.values()):
        parser.error("load profile overrides must be greater than zero")

    if args.base_url:
        if not all((args.username, args.password, args.room_id)):
            parser.error("--username, --password and --room-id are required with --base-url")
        report = run_profile(
            args.base_url.rstrip("/"),
            args.username,
            args.password,
            args.room_id,
            args.profile,
            profile_overrides=overrides,
        )
    else:
        with FixtureServer() as fixture:
            report = run_profile(
                fixture.base_url,
                fixture.username,
                fixture.password,
                fixture.room_id,
                args.profile,
                fixture.server_module,
                overrides,
            )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
