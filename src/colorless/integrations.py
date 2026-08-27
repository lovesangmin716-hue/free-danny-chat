from __future__ import annotations

import re
import threading
import time

import httpx
from google.auth import jwt as google_auth_jwt

from .config import SUPABASE_SERVICE_ROLE_KEY

GOOGLE_OAUTH2_CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
GOOGLE_TOKEN_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
GOOGLE_CERTS_MIN_TTL_SECONDS = 60
GOOGLE_CERTS_MAX_TTL_SECONDS = 24 * 60 * 60
GOOGLE_CERTS_REQUEST_TIMEOUT_SECONDS = 5.0

OUTBOUND_HTTP_CLIENT = httpx.Client(
    timeout=httpx.Timeout(30.0, connect=10.0),
    limits=httpx.Limits(max_connections=64, max_keepalive_connections=24, keepalive_expiry=30.0),
    follow_redirects=True,
)


class GoogleIdTokenVerifier:
    """Verify Google ID tokens locally while honoring Google's certificate cache TTL."""

    def __init__(self, client: httpx.Client, clock=time.monotonic) -> None:
        self.client = client
        self.clock = clock
        self.lock = threading.Lock()
        self.certificates: dict[str, str] = {}
        self.expires_at = 0.0
        self.unknown_key_refresh_at = 0.0

    @staticmethod
    def cache_ttl(cache_control: str) -> int:
        match = re.search(r"(?:^|,)\s*max-age=(\d+)", cache_control, re.IGNORECASE)
        if not match:
            return GOOGLE_CERTS_MIN_TTL_SECONDS
        return max(
            GOOGLE_CERTS_MIN_TTL_SECONDS,
            min(GOOGLE_CERTS_MAX_TTL_SECONDS, int(match.group(1))),
        )

    def fetch_certificates(self, *, force: bool = False) -> dict[str, str]:
        now = self.clock()
        with self.lock:
            if self.certificates and not force and now < self.expires_at:
                return dict(self.certificates)
            try:
                response = self.client.request(
                    "GET",
                    GOOGLE_OAUTH2_CERTS_URL,
                    timeout=GOOGLE_CERTS_REQUEST_TIMEOUT_SECONDS,
                )
            except httpx.RequestError as error:
                raise ConnectionError("Google signing certificates are temporarily unavailable.") from error
            if response.is_error:
                raise ConnectionError(f"Google signing certificate request failed with HTTP {response.status_code}.")
            try:
                payload = response.json()
            except ValueError as error:
                raise ConnectionError("Google signing certificate response was invalid.") from error
            if not isinstance(payload, dict) or not payload:
                raise ConnectionError("Google signing certificate response was invalid.")
            certificates = {
                str(key_id): str(certificate)
                for key_id, certificate in payload.items()
                if key_id and certificate
            }
            if not certificates:
                raise ConnectionError("Google signing certificate response was empty.")
            self.certificates = certificates
            self.expires_at = now + self.cache_ttl(response.headers.get("Cache-Control", ""))
            return dict(certificates)

    def verify(self, credential: str, audience: str) -> dict:
        header = google_auth_jwt.decode_header(credential)
        key_id = str(header.get("kid", "")).strip()
        if not key_id:
            raise ValueError("Google ID token key ID is missing.")

        certificates = self.fetch_certificates()
        now = self.clock()
        if key_id not in certificates:
            with self.lock:
                should_refresh = now >= self.unknown_key_refresh_at
                if should_refresh:
                    self.unknown_key_refresh_at = now + GOOGLE_CERTS_MIN_TTL_SECONDS
            if should_refresh:
                certificates = self.fetch_certificates(force=True)
        if key_id not in certificates:
            raise ValueError("Google ID token signing key is unknown.")

        payload = google_auth_jwt.decode(
            credential,
            certs=certificates,
            audience=audience,
            clock_skew_in_seconds=5,
        )
        if payload.get("iss") not in GOOGLE_TOKEN_ISSUERS:
            raise ValueError("Google ID token issuer is invalid.")
        return dict(payload)


GOOGLE_ID_TOKEN_VERIFIER = GoogleIdTokenVerifier(OUTBOUND_HTTP_CLIENT)


def verify_google_id_token_credential(credential: str, audience: str) -> dict:
    return GOOGLE_ID_TOKEN_VERIFIER.verify(credential, audience)

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
