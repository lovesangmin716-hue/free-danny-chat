from __future__ import annotations

import re
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx

from .config import (
    SHORTS_AGE_TRENDING_TOPICS,
    SHORTS_CATALOG_RETENTION_SECONDS,
    SHORTS_CATALOG_TTL_SECONDS,
    SHORTS_COLLECTION_INTERVAL_SECONDS,
    SHORTS_COLLECTION_LEASE_SECONDS,
    SHORTS_DAILY_QUOTA_BUDGET,
    SHORTS_PROFILE_TOPICS,
    YOUTH_SHORTS_BLOCKLIST,
    YOUTUBE_API_KEY,
)

OUTBOUND_HTTP_CLIENT = httpx.Client(
    timeout=httpx.Timeout(30.0, connect=10.0),
    limits=httpx.Limits(max_connections=64, max_keepalive_connections=24, keepalive_expiry=30.0),
    follow_redirects=True,
)


def youtube_duration_seconds(duration: str) -> int:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if match is None:
        return 0
    hours, minutes, seconds = (int(value or 0) for value in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def shorts_search_query_for(user: dict) -> str:
    profile = (str(user.get("age_group", "")), str(user.get("gender", "")))
    topics = SHORTS_PROFILE_TOPICS.get(profile)
    if not topics:
        return "한국어 쇼츠"
    excluded_terms = " ".join(f"-{term}" for term in YOUTH_SHORTS_BLOCKLIST)
    return f"한국어 쇼츠 {'|'.join(topics)} {excluded_terms}"


def trending_shorts_search_query(user: dict) -> str:
    topics = SHORTS_AGE_TRENDING_TOPICS.get(str(user.get("age_group", "")))
    if not topics:
        return "한국어 쇼츠"
    excluded_terms = " ".join(f"-{term}" for term in YOUTH_SHORTS_BLOCKLIST)
    return f"한국어 쇼츠 {'|'.join(topics)} {excluded_terms}"


def shorts_search_queries_for(user: dict) -> list[str]:
    excluded_terms = " ".join(f"-{term}" for term in YOUTH_SHORTS_BLOCKLIST)
    query_groups = [
        shorts_search_query_for(user),
        trending_shorts_search_query(user),
        f"한국어 쇼츠 유머|먹방|연예 {excluded_terms}",
        f"한국어 쇼츠 브이로그|여행|맛집 {excluded_terms}",
        f"한국어 쇼츠 재테크|건강|테크 {excluded_terms}",
        f"한국어 쇼츠 음악|패션|요리 {excluded_terms}",
    ]
    return list(dict.fromkeys(query_groups))


def korean_shorts_search_queries() -> list[str]:
    excluded_terms = " ".join(f"-{term}" for term in YOUTH_SHORTS_BLOCKLIST)
    query_groups = [
        "\ud55c\uad6d\uc5b4 \uc1fc\uce20",
        "\ud55c\uad6d \uc720\uba38 \uc1fc\uce20",
        "\ud55c\uad6d \uba39\ubc29 \uc1fc\uce20",
        "\ud55c\uad6d \uc5f0\uc608 \uc1fc\uce20",
        "\ud55c\uad6d \uc77c\uc0c1 \uc1fc\uce20",
    ]
    return [f"{query} {excluded_terms}" for query in query_groups]

class YoutubeCatalogError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def fetch_youtube_catalog_json(url: str, *, attempts: int = 3) -> dict:
    for attempt in range(attempts):
        try:
            response = OUTBOUND_HTTP_CLIENT.get(url, timeout=15.0)
            if response.is_error:
                code = f"http-{response.status_code}"
                if (
                    response.status_code in {403, 429}
                    or response.status_code < 500
                    or attempt + 1 >= attempts
                ):
                    raise YoutubeCatalogError(code)
            else:
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
        except httpx.RequestError as error:
            code = "network"
            if attempt + 1 >= attempts:
                raise YoutubeCatalogError(code) from error
        time.sleep((0.25 * (2 ** attempt)) + (secrets.randbelow(100) / 1000))
    raise YoutubeCatalogError("unknown")


def youtube_catalog_item(video: dict, rank_score: float, *, max_duration: int) -> dict | None:
    duration = youtube_duration_seconds(str(video.get("contentDetails", {}).get("duration", "")))
    if not 0 < duration <= max_duration or not video.get("status", {}).get("embeddable", False):
        return None
    snippet = video.get("snippet", {})
    language = str(snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or "").lower()
    if language and not language.startswith("ko"):
        return None
    text = f"{snippet.get('title', '')} {snippet.get('channelTitle', '')}".lower()
    if any(term.lower() in text for term in YOUTH_SHORTS_BLOCKLIST):
        return None
    video_id = str(video.get("id", "")).strip()
    if not video_id:
        return None
    return {
        "id": video_id,
        "title": str(snippet.get("title", "YouTube 쇼츠")),
        "channel_title": str(snippet.get("channelTitle", "YouTube")),
        "rank_score": rank_score,
    }


def collect_youtube_catalog_job(job: dict) -> list[dict]:
    if job["kind"] == "popular":
        params = {
            "key": YOUTUBE_API_KEY,
            "part": "snippet,contentDetails,status",
            "chart": "mostPopular",
            "regionCode": "KR",
            "maxResults": "50",
            "videoCategoryId": job["value"],
        }
        payload = fetch_youtube_catalog_json(
            f"https://www.googleapis.com/youtube/v3/videos?{urlencode(params)}"
        )
        candidates = [
            youtube_catalog_item(video, 2000 - index, max_duration=600)
            for index, video in enumerate(payload.get("items", []))
        ]
        return [item for item in candidates if item is not None]

    search_params = {
        "key": YOUTUBE_API_KEY,
        "part": "snippet",
        "q": job["value"],
        "type": "video",
        "maxResults": "50",
        "order": "viewCount",
        "regionCode": "KR",
        "relevanceLanguage": "ko",
        "videoDuration": "short",
        "videoEmbeddable": "true",
        "videoSyndicated": "true",
    }
    search_payload = fetch_youtube_catalog_json(
        f"https://www.googleapis.com/youtube/v3/search?{urlencode(search_params)}"
    )
    video_ids = [
        str(item.get("id", {}).get("videoId", "")).strip()
        for item in search_payload.get("items", [])
        if str(item.get("id", {}).get("videoId", "")).strip()
    ]
    if not video_ids:
        return []
    video_params = {
        "key": YOUTUBE_API_KEY,
        "part": "snippet,contentDetails,status",
        "id": ",".join(video_ids),
        "maxResults": "50",
    }
    video_payload = fetch_youtube_catalog_json(
        f"https://www.googleapis.com/youtube/v3/videos?{urlencode(video_params)}"
    )
    rank_by_id = {video_id: 1000 - index for index, video_id in enumerate(video_ids)}
    candidates = [
        youtube_catalog_item(video, rank_by_id.get(str(video.get("id", "")), 0), max_duration=180)
        for video in video_payload.get("items", [])
    ]
    return [item for item in candidates if item is not None]


class ShortsCatalogCollector:
    def __init__(self, repository, instance_id: str, *, start: bool = True) -> None:
        self.repository = repository
        self.instance_id = instance_id
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.runs = 0
        self.successes = 0
        self.failures = 0
        self.external_calls = 0
        self.items_upserted = 0
        self.lease_skips = 0
        self.feed_requests = 0
        self.catalog_hits = 0
        self.stale_hits = 0
        self.emergency_hits = 0
        self.last_run_ms = 0.0
        self.jobs = [
            {"name": f"search-{index}", "kind": "search", "value": query, "quota": 101}
            for index, query in enumerate(korean_shorts_search_queries())
        ]
        self.thread = threading.Thread(target=self._run, name="shorts-catalog-collector", daemon=True)
        if start:
            self.start()

    def start(self) -> None:
        if not self.thread.is_alive() and self.thread.ident is None:
            self.thread.start()

    def run_once(self) -> bool:
        if not YOUTUBE_API_KEY or not self.jobs:
            return False
        started = time.monotonic()
        # search.list (100 units) + videos.list (1 unit), reserved atomically.
        lease = self.repository.acquire_shorts_collection_lease(
            self.instance_id,
            time.time(),
            SHORTS_COLLECTION_LEASE_SECONDS,
            101,
            SHORTS_DAILY_QUOTA_BUDGET,
        )
        if lease is None:
            with self.lock:
                self.lease_skips += 1
            return False
        job_index = int(lease.get("next_job_index", 0)) % len(self.jobs)
        job = self.jobs[job_index]
        with self.lock:
            self.runs += 1
            self.external_calls += 1 if job["kind"] == "popular" else 2
        try:
            items = collect_youtube_catalog_job(job)
            now = time.time()
            self.repository.upsert_shorts_catalog(items, str(job["name"]), now, SHORTS_CATALOG_TTL_SECONDS)
            self.repository.prune_shorts_catalog(now - SHORTS_CATALOG_RETENTION_SECONDS)
            self.repository.finish_shorts_collection(
                self.instance_id,
                now=now,
                next_job=(job_index + 1) % len(self.jobs),
                success=True,
            )
            with self.lock:
                self.successes += 1
                self.items_upserted += len(items)
            return True
        except YoutubeCatalogError as error:
            circuit_seconds = 15 * 60 if error.code in {"http-429", "http-403"} else 2 * 60
            self.repository.finish_shorts_collection(
                self.instance_id,
                now=time.time(),
                next_job=job_index,
                success=False,
                error=error.code,
                circuit_seconds=circuit_seconds,
            )
            with self.lock:
                self.failures += 1
            return False
        except Exception:
            self.repository.finish_shorts_collection(
                self.instance_id,
                now=time.time(),
                next_job=job_index,
                success=False,
                error="collector-error",
                circuit_seconds=2 * 60,
            )
            with self.lock:
                self.failures += 1
            return False
        finally:
            with self.lock:
                self.last_run_ms = round((time.monotonic() - started) * 1000, 3)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.run_once()
            self.wake_event.wait(SHORTS_COLLECTION_INTERVAL_SECONDS)
            self.wake_event.clear()

    def snapshot(self) -> dict:
        with self.lock:
            runtime = {
                "runs": self.runs,
                "successes": self.successes,
                "failures": self.failures,
                "external_calls": self.external_calls,
                "items_upserted": self.items_upserted,
                "lease_skips": self.lease_skips,
                "feed_requests": self.feed_requests,
                "catalog_hits": self.catalog_hits,
                "stale_hits": self.stale_hits,
                "emergency_hits": self.emergency_hits,
                "catalog_hit_rate": round(self.catalog_hits / self.feed_requests, 4) if self.feed_requests else 0,
                "last_run_ms": self.last_run_ms,
            }
        try:
            return {**runtime, **self.repository.shorts_catalog_status(time.time())}
        except Exception:
            return {**runtime, "status_error": True}

    def record_feed(self, *, catalog_hit: bool, stale_hit: bool, emergency_hit: bool) -> None:
        with self.lock:
            self.feed_requests += 1
            self.catalog_hits += int(catalog_hit)
            self.stale_hits += int(stale_hit)
            self.emergency_hits += int(emergency_hit)

    def close(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
