from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from datetime import datetime

from .config import EVENT_POLL_INTERVAL_SECONDS, PRESENCE_TTL_SECONDS
from .observability import SSE_METRICS
from .utils import utc_now, utc_now_iso


class DurableEventBroker:
    """Shared database event log with local fan-out and retryable publishing."""

    def __init__(
        self,
        repository,
        instance_id: str,
        presence_recipients,
        state_refresh,
        *,
        deliver,
        cleanup=lambda: None,
    ) -> None:
        self.repository = repository
        self.instance_id = instance_id
        self.presence_recipients = presence_recipients
        self.state_refresh = state_refresh
        self.deliver = deliver
        self.cleanup = cleanup
        self.cursor = repository.latest_event_sequence()
        self.lock = threading.Lock()
        self.outbox: deque[tuple[dict, set[str]]] = deque(maxlen=10_000)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.next_presence_cleanup = time.monotonic()
        self.thread = threading.Thread(target=self._run, name="durable-event-broker", daemon=True)
        self.thread.start()

    def publish(self, event: dict, recipients: set[str]) -> dict:
        if not recipients:
            return event
        durable_candidate = {
            **event,
            "event_id": str(event.get("event_id") or uuid.uuid4().hex),
            "occurred_at": event.get("occurred_at") or utc_now_iso(),
        }
        try:
            published = self.repository.publish_event(durable_candidate, recipients, self.instance_id)
            SSE_METRICS.increment("events_published_total")
            self.wake_event.set()
            return published
        except Exception:
            SSE_METRICS.increment("event_publish_failures_total")
            with self.lock:
                self.outbox.append((durable_candidate, set(recipients)))
            self.deliver(durable_candidate, recipients)
            self.wake_event.set()
            return durable_candidate

    def replay(self, username: str, after_sequence: int, *, limit: int = 500) -> list[dict]:
        events = self.repository.events_for_user_after(username, after_sequence, limit=limit)
        if events:
            SSE_METRICS.increment("events_replayed_total", len(events))
        return events

    def _retry_outbox(self) -> None:
        with self.lock:
            pending = list(self.outbox)
            self.outbox.clear()
        for index, (event, recipients) in enumerate(pending):
            try:
                self.repository.publish_event(event, recipients, self.instance_id)
                SSE_METRICS.increment("events_published_total")
            except Exception:
                SSE_METRICS.increment("event_publish_failures_total")
                with self.lock:
                    for item in pending[index:]:
                        self.outbox.append(item)
                return

    @staticmethod
    def _latency_ms(event: dict) -> float:
        try:
            occurred_at = datetime.fromisoformat(str(event.get("occurred_at", "")).replace("Z", "+00:00"))
            return (utc_now() - occurred_at).total_seconds() * 1000
        except (TypeError, ValueError):
            return 0.0

    def _consume(self) -> None:
        while True:
            events = self.repository.list_events_after(self.cursor, limit=500)
            if not events:
                return
            for event, recipients in events:
                revision = int(event.get("revision", 0))
                if event.get("origin_instance_id") != self.instance_id:
                    self.state_refresh()
                self.deliver(event, recipients)
                self.cursor = max(self.cursor, revision)
                SSE_METRICS.increment("events_consumed_total")
                SSE_METRICS.record_delivery_latency(self._latency_ms(event))
            if len(events) < 500:
                return

    def _cleanup_presence(self) -> None:
        if time.monotonic() < self.next_presence_cleanup:
            return
        self.next_presence_cleanup = time.monotonic() + min(5, max(1, PRESENCE_TTL_SECONDS // 3))
        self.cleanup()
        for username, presence in self.repository.cleanup_expired_presence():
            self.publish(
                {"type": "presence_updated", "username": username, "presence": presence},
                self.presence_recipients(username),
            )

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._retry_outbox()
                self._consume()
                self._cleanup_presence()
            except Exception:
                SSE_METRICS.increment("event_consume_failures_total")
            self.wake_event.wait(EVENT_POLL_INTERVAL_SECONDS)
            self.wake_event.clear()

    def close(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join(timeout=2)
