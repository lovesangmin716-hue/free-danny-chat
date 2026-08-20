from __future__ import annotations

import httpx

from .config import SUPABASE_SERVICE_ROLE_KEY

OUTBOUND_HTTP_CLIENT = httpx.Client(
    timeout=httpx.Timeout(30.0, connect=10.0),
    limits=httpx.Limits(max_connections=64, max_keepalive_connections=24, keepalive_expiry=30.0),
    follow_redirects=True,
)

def fetch_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> object:
    try:
        response = OUTBOUND_HTTP_CLIENT.request(method, url, headers=headers, content=data, timeout=15.0)
        if response.is_error:
            raise ValueError(response.text or f"HTTP {response.status_code}")
        return response.json() if response.content else {}
    except httpx.RequestError as error:
        raise ConnectionError(str(error)) from error


def fetch_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> bytes:
    try:
        response = OUTBOUND_HTTP_CLIENT.request(method, url, headers=headers, content=data)
        if response.is_error:
            raise ValueError(response.text or f"HTTP {response.status_code}")
        return response.content
    except httpx.RequestError as error:
        raise ConnectionError(str(error)) from error






def supabase_headers(content_type: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers
