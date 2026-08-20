from __future__ import annotations

import threading
import time
from collections import OrderedDict


class BoundedTTLCache:
    def __init__(self, max_entries: int = 256, ttl_seconds: int = 300) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.lock = threading.Lock()
        self.entries: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self.in_flight: dict[str, threading.Event] = {}

    def get_or_fetch(self, key: str, fetcher) -> object:
        while True:
            now = time.monotonic()
            with self.lock:
                cached = self.entries.get(key)
                if cached is not None and cached[0] > now:
                    self.entries.move_to_end(key)
                    return cached[1]
                if cached is not None:
                    self.entries.pop(key, None)

                waiter = self.in_flight.get(key)
                if waiter is None:
                    waiter = threading.Event()
                    self.in_flight[key] = waiter
                    is_owner = True
                else:
                    is_owner = False

            if is_owner:
                break
            if not waiter.wait(20):
                raise ConnectionError("Cached upstream request timed out")

        try:
            value = fetcher()
        except Exception:
            with self.lock:
                self.in_flight.pop(key, None)
                waiter.set()
            raise

        with self.lock:
            self.entries[key] = (time.monotonic() + self.ttl_seconds, value)
            self.entries.move_to_end(key)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)
            self.in_flight.pop(key, None)
            waiter.set()
        return value
