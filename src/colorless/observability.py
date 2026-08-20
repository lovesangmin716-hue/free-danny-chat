from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from urllib.parse import urlparse

from .config import STRUCTURED_LOGS_ENABLED

def process_rss_bytes() -> int:
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            get_current_process = ctypes.windll.kernel32.GetCurrentProcess
            get_current_process.restype = wintypes.HANDLE
            get_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
            get_memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
            get_memory_info.restype = wintypes.BOOL
            handle = get_current_process()
            if get_memory_info(handle, ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
            return 0
        statm = Path("/proc/self/statm")
        if statm.exists():
            resident_pages = int(statm.read_text(encoding="ascii").split()[1])
            return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return 0
    return 0


class SseRuntimeMetrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started_at = time.monotonic()
        self.active = 0
        self.accepted_total = 0
        self.rejected_total = 0
        self.disconnected_total = 0
        self.events_enqueued_total = 0
        self.queue_drops_total = 0
        self.heartbeats_total = 0
        self.events_published_total = 0
        self.event_publish_failures_total = 0
        self.events_consumed_total = 0
        self.event_consume_failures_total = 0
        self.events_replayed_total = 0
        self.reconnects_total = 0
        self.delivery_latencies_ms: deque[float] = deque(maxlen=2_000)

    def increment(self, name: str, amount: int = 1) -> None:
        with self.lock:
            setattr(self, name, max(0, int(getattr(self, name)) + amount))

    def snapshot(self) -> dict:
        with self.lock:
            ordered_latencies = sorted(self.delivery_latencies_ms)
            p95_index = max(0, math.ceil(len(ordered_latencies) * 0.95) - 1)
            return {
                "active": self.active,
                "accepted_total": self.accepted_total,
                "rejected_total": self.rejected_total,
                "disconnected_total": self.disconnected_total,
                "events_enqueued_total": self.events_enqueued_total,
                "queue_drops_total": self.queue_drops_total,
                "heartbeats_total": self.heartbeats_total,
                "events_published_total": self.events_published_total,
                "event_publish_failures_total": self.event_publish_failures_total,
                "events_consumed_total": self.events_consumed_total,
                "event_consume_failures_total": self.event_consume_failures_total,
                "events_replayed_total": self.events_replayed_total,
                "reconnects_total": self.reconnects_total,
                "event_delivery_p95_ms": round(ordered_latencies[p95_index], 3) if ordered_latencies else 0,
                "uptime_seconds": round(time.monotonic() - self.started_at, 3),
            }

    def record_delivery_latency(self, milliseconds: float) -> None:
        with self.lock:
            self.delivery_latencies_ms.append(max(0.0, milliseconds))


SSE_METRICS = SseRuntimeMetrics()


class RequestRuntimeMetrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.accepted_total = 0
        self.rejected_total = 0
        self.header_timeouts_total = 0
        self.body_timeouts_total = 0
        self.body_reader_rejections_total = 0
        self.active_body_readers = 0
        self.completed_total = 0
        self.client_errors_total = 0
        self.server_errors_total = 0
        self.request_bytes_total = 0
        self.response_bytes_total = 0
        self.latencies_ms: deque[float] = deque(maxlen=10_000)
        self.routes: OrderedDict[str, dict] = OrderedDict()

    def increment(self, name: str, amount: int = 1) -> None:
        with self.lock:
            setattr(self, name, max(0, int(getattr(self, name)) + amount))

    def snapshot(self) -> dict:
        with self.lock:
            total_latency = self._latency_summary(self.latencies_ms)
            route_metrics = {
                route: {
                    "count": values["count"],
                    "server_errors": values["server_errors"],
                    "error_rate": round(values["server_errors"] / values["count"], 6) if values["count"] else 0,
                    "request_bytes": values["request_bytes"],
                    "response_bytes": values["response_bytes"],
                    "latency_ms": self._latency_summary(values["latencies"]),
                }
                for route, values in self.routes.items()
            }
            return {
                "active": self.active,
                "accepted_total": self.accepted_total,
                "rejected_total": self.rejected_total,
                "header_timeouts_total": self.header_timeouts_total,
                "body_timeouts_total": self.body_timeouts_total,
                "body_reader_rejections_total": self.body_reader_rejections_total,
                "active_body_readers": self.active_body_readers,
                "completed_total": self.completed_total,
                "client_errors_total": self.client_errors_total,
                "server_errors_total": self.server_errors_total,
                "server_error_rate": round(self.server_errors_total / self.completed_total, 6) if self.completed_total else 0,
                "request_bytes_total": self.request_bytes_total,
                "response_bytes_total": self.response_bytes_total,
                "latency_ms": total_latency,
                "routes": route_metrics,
            }

    @staticmethod
    def _latency_summary(values) -> dict:
        ordered = sorted(values)
        if not ordered:
            return {"p50": 0, "p95": 0, "p99": 0, "max": 0}
        value_at = lambda quantile: ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]
        return {
            "p50": round(value_at(0.50), 3),
            "p95": round(value_at(0.95), 3),
            "p99": round(value_at(0.99), 3),
            "max": round(ordered[-1], 3),
        }

    def record(
        self,
        route: str,
        status: int,
        latency_ms: float,
        request_bytes: int,
        response_bytes: int,
    ) -> None:
        with self.lock:
            self.completed_total += 1
            self.client_errors_total += int(400 <= status < 500)
            self.server_errors_total += int(status >= 500)
            self.request_bytes_total += max(0, request_bytes)
            self.response_bytes_total += max(0, response_bytes)
            self.latencies_ms.append(max(0.0, latency_ms))
            values = self.routes.pop(route, None)
            if values is None:
                values = {
                    "count": 0,
                    "server_errors": 0,
                    "request_bytes": 0,
                    "response_bytes": 0,
                    "latencies": deque(maxlen=2_000),
                }
            values["count"] += 1
            values["server_errors"] += int(status >= 500)
            values["request_bytes"] += max(0, request_bytes)
            values["response_bytes"] += max(0, response_bytes)
            values["latencies"].append(max(0.0, latency_ms))
            self.routes[route] = values
            while len(self.routes) > 100:
                self.routes.popitem(last=False)

def normalized_request_route(method: str, request_target: str) -> str:
    path = urlparse(request_target).path
    if re.fullmatch(r"/uploads/upload_[0-9a-f]{32}\.[a-z0-9]+", path):
        path = "/uploads/:object"
    else:
        path = re.sub(r"/rooms/room_[0-9a-f]{8}/members", "/rooms/:room_id/members", path)
        path = re.sub(r"/profile-art/user_[0-9a-f]{8}/thumbnail", "/profile-art/:user_id/thumbnail", path)
    return f"{method.upper()} {path[:200]}"


def safe_user_identifier(username: str) -> str:
    return hashlib.sha256(username.encode("utf-8")).hexdigest()[:12] if username else ""


def process_open_file_descriptors() -> int:
    descriptor_path = Path("/proc/self/fd")
    if not descriptor_path.is_dir():
        return 0
    try:
        return len(list(descriptor_path.iterdir()))
    except OSError:
        return 0


def write_structured_log(payload: dict) -> None:
    if not STRUCTURED_LOGS_ENABLED:
        return
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
