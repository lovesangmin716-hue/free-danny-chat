from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import time
from urllib.parse import quote, urlencode, urlparse

from .config import (
    APP_NAME,
    NAVER_MAP_CLIENT_ID,
    NAVER_SEARCH_CLIENT_ID,
    NAVER_SEARCH_CLIENT_SECRET,
    PLACE_TOKEN_SECRET,
)
from .integrations import fetch_json


PLACE_TOKEN_TTL_SECONDS = 15 * 60
NAVER_LOCAL_SEARCH_URL = "https://openapi.naver.com/v1/search/local.json"
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


DEMO_PLACES = (
    {
        "provider": "demo",
        "name": "롯데월드",
        "category": "여행,명소 > 테마파크",
        "address": "서울특별시 송파구 잠실동 40-1",
        "road_address": "서울특별시 송파구 올림픽로 240",
        "latitude": 37.5111158,
        "longitude": 127.098167,
        "verification_radius_m": 500,
    },
    {
        "provider": "demo",
        "name": "석촌호수",
        "category": "여행,명소 > 호수",
        "address": "서울특별시 송파구 잠실동",
        "road_address": "서울특별시 송파구 잠실로 148",
        "latitude": 37.5087532,
        "longitude": 127.1000739,
        "verification_radius_m": 450,
    },
    {
        "provider": "demo",
        "name": "잠실역",
        "category": "교통 > 지하철역",
        "address": "서울특별시 송파구 잠실동",
        "road_address": "서울특별시 송파구 올림픽로 265",
        "latitude": 37.5132612,
        "longitude": 127.1001336,
        "verification_radius_m": 250,
    },
    {
        "provider": "demo",
        "name": "경복궁",
        "category": "여행,명소 > 궁궐",
        "address": "서울특별시 종로구 세종로 1-1",
        "road_address": "서울특별시 종로구 사직로 161",
        "latitude": 37.579617,
        "longitude": 126.977041,
        "verification_radius_m": 500,
    },
    {
        "provider": "demo",
        "name": "부산역",
        "category": "교통 > 기차역",
        "address": "부산광역시 동구 초량동 1187-1",
        "road_address": "부산광역시 동구 중앙대로 206",
        "latitude": 35.115225,
        "longitude": 129.041384,
        "verification_radius_m": 300,
    },
)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _clean_copy(value: object, limit: int) -> str:
    text = html.unescape(HTML_TAG_PATTERN.sub("", str(value or ""))).strip()
    return " ".join(text.split())[:limit]


def _verification_radius(category: str, name: str) -> int:
    copy = f"{category} {name}"
    if any(word in copy for word in ("테마파크", "궁궐", "공원", "호수", "대학교")):
        return 500
    if any(word in copy for word in ("역", "터미널", "공항", "경기장")):
        return 300
    return 200


def _place_key(place: dict) -> str:
    canonical = "|".join(
        (
            str(place.get("provider", "naver")),
            str(place.get("name", "")).casefold(),
            str(place.get("road_address") or place.get("address") or "").casefold(),
            f"{float(place.get('latitude', 0)):.6f}",
            f"{float(place.get('longitude', 0)):.6f}",
        )
    )
    return f"place_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


class PlaceService:
    def __init__(self) -> None:
        self.map_client_id = NAVER_MAP_CLIENT_ID
        self.search_enabled = bool(NAVER_SEARCH_CLIENT_ID and NAVER_SEARCH_CLIENT_SECRET)
        self.token_secret = PLACE_TOKEN_SECRET.encode("utf-8")

    def public_config(self) -> dict:
        return {
            "map_client_id": self.map_client_id,
            "map_enabled": bool(self.map_client_id),
            "search_provider": "naver" if self.search_enabled else "demo",
            "verification_ttl_seconds": 3 * 60 * 60,
        }

    def _normalize_place(self, place: dict) -> dict:
        normalized = {
            "provider": _clean_copy(place.get("provider", "naver"), 20) or "naver",
            "name": _clean_copy(place.get("name"), 80),
            "category": _clean_copy(place.get("category"), 120),
            "address": _clean_copy(place.get("address"), 160),
            "road_address": _clean_copy(place.get("road_address"), 160),
            "latitude": round(float(place.get("latitude", 0)), 7),
            "longitude": round(float(place.get("longitude", 0)), 7),
            "verification_radius_m": max(50, min(2000, int(place.get("verification_radius_m", 200)))),
        }
        if not normalized["name"]:
            raise ValueError("장소 이름이 없습니다.")
        if not 32 <= normalized["latitude"] <= 39.5 or not 124 <= normalized["longitude"] <= 132:
            raise ValueError("대한민국 내 장소 좌표가 아닙니다.")
        normalized["key"] = _clean_copy(place.get("key"), 40) or _place_key(normalized)
        external_url = _clean_copy(
            place.get("external_url") or f"https://map.naver.com/p/search/{quote(normalized['name'])}",
            500,
        )
        if urlparse(external_url).scheme not in {"http", "https"}:
            external_url = f"https://map.naver.com/p/search/{quote(normalized['name'])}"
        normalized["external_url"] = external_url
        return normalized

    def issue_token(self, place: dict) -> str:
        payload = {**self._normalize_place(place), "exp": int(time.time()) + PLACE_TOKEN_TTL_SECONDS}
        encoded = _b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = _b64encode(hmac.new(self.token_secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def decode_token(self, token: str) -> dict:
        try:
            encoded, signature = token.split(".", 1)
            expected = _b64encode(hmac.new(self.token_secret, encoded.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise ValueError("장소 선택 정보가 올바르지 않습니다.")
            payload = json.loads(_b64decode(encoded).decode("utf-8"))
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("장소 선택 정보가 올바르지 않습니다.") from error
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("장소 검색 결과가 만료되었습니다. 다시 검색해 주세요.")
        payload.pop("exp", None)
        return self._normalize_place(payload)

    def _naver_places(self, query: str) -> list[dict]:
        params = urlencode({"query": query, "display": 5, "start": 1, "sort": "random"})
        payload = fetch_json(
            f"{NAVER_LOCAL_SEARCH_URL}?{params}",
            headers={
                "X-Naver-Client-Id": NAVER_SEARCH_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_SEARCH_CLIENT_SECRET,
                "User-Agent": f"{APP_NAME}/1.0",
            },
            timeout_seconds=5.0,
        )
        items = payload.get("items", []) if isinstance(payload, dict) else []
        places: list[dict] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                longitude = int(str(item.get("mapx", "0"))) / 10_000_000
                latitude = int(str(item.get("mapy", "0"))) / 10_000_000
            except ValueError:
                continue
            name = _clean_copy(item.get("title"), 80)
            category = _clean_copy(item.get("category"), 120)
            place = {
                "provider": "naver",
                "name": name,
                "category": category,
                "address": item.get("address", ""),
                "road_address": item.get("roadAddress", ""),
                "latitude": latitude,
                "longitude": longitude,
                "verification_radius_m": _verification_radius(category, name),
                "external_url": item.get("link") or f"https://map.naver.com/p/search/{quote(name)}",
            }
            try:
                places.append(self._normalize_place(place))
            except ValueError:
                continue
        return places

    def _demo_places(self, query: str) -> list[dict]:
        normalized_query = query.casefold().strip()
        matches = [
            self._normalize_place(dict(place))
            for place in DEMO_PLACES
            if not normalized_query
            or normalized_query in str(place["name"]).casefold()
            or normalized_query in str(place["category"]).casefold()
            or normalized_query in str(place["road_address"]).casefold()
        ]
        return matches[:5]

    def search(self, query: str) -> dict:
        normalized_query = " ".join(str(query or "").strip().split())[:80]
        if self.search_enabled and normalized_query:
            try:
                places = self._naver_places(normalized_query)
                provider = "naver"
            except (ConnectionError, ValueError):
                places = self._demo_places(normalized_query)
                provider = "demo"
        else:
            places = self._demo_places(normalized_query)
            provider = "demo"
        return {
            "items": [{**place, "place_token": self.issue_token(place)} for place in places],
            "provider": provider,
            "query": normalized_query,
        }
