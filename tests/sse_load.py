from __future__ import annotations

import argparse
import http.client
import json
import math
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def json_request(base_url: str, path: str, method: str = "GET", payload: dict | None = None, cookie: str = "") -> tuple[int, dict, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8")), dict(response.headers)


def login(base_url: str, username: str, password: str) -> str:
    _, _, headers = json_request(base_url, "/login", "POST", {"username": username, "password": password})
    cookie = SimpleCookie()
    cookie.load(headers.get("Set-Cookie", ""))
    morsel = cookie.get("codex_talk_session")
    if morsel is None:
        raise RuntimeError("login did not return a session cookie")
    return f"codex_talk_session={morsel.value}"


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile_value) - 1)]


def run_load(base_url: str, username: str, password: str, connections: int, health_requests: int) -> dict:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("base URL must be an http:// URL")
    port = parsed.port or 80
    cookie = login(base_url, username, password)
    _, baseline_metrics, _ = json_request(base_url, "/metrics")
    baseline_active = int(baseline_metrics["sse"]["active"])
    stop = threading.Event()
    ready = threading.Condition()
    streams: list[http.client.HTTPConnection] = []
    failures: list[str] = []

    def connect_stream() -> None:
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=15)
        try:
            connection.request("GET", "/events", headers={"Cookie": cookie, "Accept": "text/event-stream"})
            response = connection.getresponse()
            if response.status != 200:
                failures.append(f"SSE HTTP {response.status}")
                return
            response.readline()
            with ready:
                streams.append(connection)
                ready.notify_all()
            stop.wait()
        except Exception as error:  # noqa: BLE001 - load tool reports every connection failure.
            failures.append(str(error))
        finally:
            connection.close()

    threads = [threading.Thread(target=connect_stream, daemon=True) for _ in range(connections)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 20
    with ready:
        while len(streams) + len(failures) < connections and time.monotonic() < deadline:
            ready.wait(timeout=0.2)

    health_latencies_ms: list[float] = []
    health_failures = 0
    for _ in range(health_requests):
        started = time.perf_counter()
        try:
            status, _, _ = json_request(base_url, "/health")
            if status != 200:
                health_failures += 1
        except Exception:  # noqa: BLE001 - a failed probe is part of the result.
            health_failures += 1
        health_latencies_ms.append((time.perf_counter() - started) * 1000)

    _, active_metrics, _ = json_request(base_url, "/metrics")
    stop.set()
    for connection in list(streams):
        connection.close()
    for thread in threads:
        thread.join(timeout=1)

    reclaim_deadline = time.monotonic() + int(active_metrics["limits"]["heartbeat_seconds"]) * 2 + 3
    reclaimed_metrics = active_metrics
    while time.monotonic() < reclaim_deadline:
        _, reclaimed_metrics, _ = json_request(base_url, "/metrics")
        if int(reclaimed_metrics["sse"]["active"]) <= baseline_active:
            break
        time.sleep(0.25)

    result = {
        "requested_connections": connections,
        "accepted_connections": len(streams),
        "connection_failures": failures,
        "health_requests": health_requests,
        "health_failures": health_failures,
        "health_p50_ms": round(percentile(health_latencies_ms, 0.50), 3),
        "health_p95_ms": round(percentile(health_latencies_ms, 0.95), 3),
        "active_metrics": active_metrics,
        "reclaimed_metrics": reclaimed_metrics,
        "resources_reclaimed": int(reclaimed_metrics["sse"]["active"]) <= baseline_active,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Open SSE connections while measuring health endpoint latency and cleanup.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--connections", type=int, default=20)
    parser.add_argument("--health-requests", type=int, default=40)
    args = parser.parse_args()
    result = run_load(args.base_url.rstrip("/"), args.username, args.password, args.connections, args.health_requests)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if (
        result["accepted_connections"] == args.connections
        and result["health_failures"] == 0
        and result["resources_reclaimed"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
