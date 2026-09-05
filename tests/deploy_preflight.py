from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).parents[1]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def render_value(source: str, key: str, expected: str) -> bool:
    pattern = (
        rf"(?m)^\s*-\s+key:\s*{re.escape(key)}\s*$\r?\n"
        rf"\s+value:\s*[\"']?{re.escape(expected)}[\"']?\s*$"
    )
    return re.search(pattern, source) is not None


def validate_source() -> list[str]:
    failures: list[str] = []
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    schema = (ROOT / "src/colorless/database/supabase-schema.sql").read_text(encoding="utf-8")
    packaged_js = ROOT / "src/colorless/web/assets/js/main.js"

    require("healthCheckPath: /ready" in render, "Render health check must use /ready.", failures)
    require("autoDeployTrigger: checksPass" in render, "Render deploys must wait for CI checks.", failures)
    require(render_value(render, "REQUIRE_SUPABASE", "true"), "Render must require Supabase.", failures)
    require(render_value(render, "LOCAL_SIGNUP_ENABLED", "false"), "Render must disable local signup until SMS delivery exists.", failures)
    require(render_value(render, "SOCIAL_DEMO_LOGIN_ENABLED", "false"), "Render must disable demo login.", failures)
    require("create table if not exists public.accounts" in schema, "Supabase accounts table is missing.", failures)
    require("users_account_identity_limit" in schema, "Supabase identity-limit trigger is missing.", failures)
    require("pg_advisory_xact_lock" in schema, "Supabase identity creation must be concurrency-safe.", failures)
    require("colorless_create_account_session" in schema, "Account session RPC is missing.", failures)
    require("colorless_switch_session_identity" in schema, "Identity switch RPC is missing.", failures)
    require("colorless_account_identity_integrity" in schema, "Account identity integrity RPC is missing.", failures)
    require("colorless_insert_message_v2" in schema, "Versioned message insert RPC is missing.", failures)
    require(
        "revoke execute on function public.colorless_insert_message_v2" in schema
        and "grant execute on function public.colorless_insert_message_v2" in schema,
        "Versioned message insert RPC permissions are incomplete.",
        failures,
    )
    require("create table if not exists public.message_reactions" in schema, "Message reactions table is missing.", failures)
    require("colorless_edit_message" in schema, "Message edit RPC is missing.", failures)
    require("colorless_toggle_message_reaction" in schema, "Message reaction RPC is missing.", failures)
    require("colorless_delete_message" in schema, "Message delete RPC is missing.", failures)
    require("colorless_unread_counts" in schema, "Unread-count RPC is missing.", failures)
    require(
        "revoke execute on function public.colorless_edit_message" in schema
        and "grant execute on function public.colorless_edit_message" in schema,
        "Message edit RPC permissions are incomplete.",
        failures,
    )
    require(
        "revoke execute on function public.colorless_toggle_message_reaction" in schema
        and "grant execute on function public.colorless_toggle_message_reaction" in schema,
        "Message reaction RPC permissions are incomplete.",
        failures,
    )
    require(
        "revoke execute on function public.colorless_delete_message" in schema
        and "grant execute on function public.colorless_delete_message" in schema,
        "Message delete RPC permissions are incomplete.",
        failures,
    )
    require(
        "revoke execute on function public.colorless_unread_counts" in schema
        and "grant execute on function public.colorless_unread_counts" in schema,
        "Unread-count RPC permissions are incomplete.",
        failures,
    )
    require("users_account_idx" in schema, "Account identity index is missing.", failures)
    require("--environment --remote" in render, "Render build must validate the production Supabase schema.", failures)
    require(packaged_js.exists() and packaged_js.stat().st_size > 0, "Packaged frontend bundle is missing.", failures)
    return failures


def validate_environment() -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    require(os.getenv("REQUIRE_SUPABASE", "").lower() == "true", "REQUIRE_SUPABASE must be true.", failures)
    require(urlparse(public_base_url).scheme == "https" and bool(urlparse(public_base_url).netloc), "PUBLIC_BASE_URL must be an HTTPS origin.", failures)
    require(urlparse(supabase_url).scheme == "https" and bool(urlparse(supabase_url).netloc), "SUPABASE_URL must be HTTPS.", failures)
    require(len(service_key) >= 20, "SUPABASE_SERVICE_ROLE_KEY is missing or too short.", failures)
    require(os.getenv("SOCIAL_DEMO_LOGIN_ENABLED", "true").lower() == "false", "SOCIAL_DEMO_LOGIN_ENABLED must be false.", failures)
    require(os.getenv("LOCAL_SIGNUP_ENABLED", "true").lower() == "false", "LOCAL_SIGNUP_ENABLED must remain false until SMS delivery is configured.", failures)
    require(os.getenv("PHONE_VERIFICATION_MODE", "") == "prod", "PHONE_VERIFICATION_MODE must be prod.", failures)
    if not (os.getenv("GOOGLE_CLIENT_ID", "").strip() and os.getenv("GOOGLE_CLIENT_SECRET", "").strip()):
        warnings.append("Google OAuth is incomplete; GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are both required.")
    if not os.getenv("KAKAO_REST_API_KEY", "").strip():
        warnings.append("KAKAO_REST_API_KEY is missing; Kakao login will be disabled.")
    elif not os.getenv("KAKAO_CLIENT_SECRET", "").strip():
        warnings.append("KAKAO_CLIENT_SECRET is missing; token issuance fails unless Client Secret is disabled in Kakao Developers.")
    return failures, warnings


def supabase_request(path: str, *, method: str = "GET", payload: dict | None = None):
    base_url = os.environ["SUPABASE_URL"].rstrip("/")
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            encoded = response.read()
    except HTTPError as error:
        raise RuntimeError(f"{method} {path.split('?', 1)[0]} returned HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"Could not connect to the configured Supabase project: {error.reason}") from error
    return json.loads(encoded) if encoded else None


def validate_remote_supabase() -> list[str]:
    failures: list[str] = []
    try:
        accounts = supabase_request("/rest/v1/accounts?select=id&limit=1")
        identities = supabase_request("/rest/v1/users?select=id,account_id&limit=1")
        sessions = supabase_request("/rest/v1/sessions?select=token_hash,account_id,active_user_id&limit=1")
        message_reactions = supabase_request("/rest/v1/message_reactions?select=message_id,user_id,emoji&limit=1")
        insert_probe = supabase_request(
            "/rest/v1/rpc/colorless_insert_message_v2",
            method="POST",
            payload={
                "message_data": {
                    "id": "__colorless_preflight_missing_message__",
                    "room_id": "__colorless_preflight_missing_room__",
                    "username": "__colorless_preflight_missing_user__",
                    "text": "",
                    "timestamp": "1970-01-01T00:00:00+00:00",
                },
                "sender_user_id": "__colorless_preflight_missing_user__",
                "room_data": {},
                "keep_count": 1,
            },
        )
        edit_probe = supabase_request(
            "/rest/v1/rpc/colorless_edit_message",
            method="POST",
            payload={
                "message_room_id": "__colorless_preflight_missing_room__",
                "target_message_id": "__colorless_preflight_missing_message__",
                "editor_user_id": "__colorless_preflight_missing_user__",
                "edited_text": "",
                "edited_timestamp": "1970-01-01T00:00:00+00:00",
            },
        )
        reaction_probe = supabase_request(
            "/rest/v1/rpc/colorless_toggle_message_reaction",
            method="POST",
            payload={
                "reaction_room_id": "__colorless_preflight_missing_room__",
                "reaction_message_id": "__colorless_preflight_missing_message__",
                "reaction_user_id": "__colorless_preflight_missing_user__",
                "reaction_emoji": "👍",
                "reaction_created_at": "1970-01-01T00:00:00+00:00",
                "desired_reacted": False,
            },
        )
        delete_probe = supabase_request(
            "/rest/v1/rpc/colorless_delete_message",
            method="POST",
            payload={
                "message_room_id": "__colorless_preflight_missing_room__",
                "target_message_id": "__colorless_preflight_missing_message__",
                "deleting_user_id": "__colorless_preflight_missing_user__",
            },
        )
        unread_probe = supabase_request(
            "/rest/v1/rpc/colorless_unread_counts",
            method="POST",
            payload={
                "room_viewers": [{
                    "room_id": "__colorless_preflight_missing_room__",
                    "viewer_user_id": "__colorless_preflight_missing_user__",
                }],
            },
        )
        integrity = supabase_request(
            "/rest/v1/rpc/colorless_account_identity_integrity",
            method="POST",
            payload={},
        )
        require(isinstance(accounts, list), "Supabase accounts table is not queryable.", failures)
        require(isinstance(identities, list), "Supabase account identities are not queryable.", failures)
        require(isinstance(sessions, list), "Supabase account sessions are not queryable.", failures)
        require(isinstance(message_reactions, list), "Supabase message reactions are not queryable.", failures)
        require(
            isinstance(insert_probe, dict) and insert_probe.get("error") == "not_found",
            "Supabase message insert RPC is unavailable or returned an invalid probe response.",
            failures,
        )
        require(
            isinstance(edit_probe, dict) and edit_probe.get("error") == "not_found",
            "Supabase message edit RPC is unavailable or returned an invalid probe response.",
            failures,
        )
        require(
            isinstance(reaction_probe, dict) and reaction_probe.get("error") == "not_found",
            "Supabase message reaction RPC is unavailable or returned an invalid probe response.",
            failures,
        )
        require(
            isinstance(delete_probe, dict) and delete_probe.get("error") == "not_found",
            "Supabase message delete RPC is unavailable or returned an invalid probe response.",
            failures,
        )
        require(
            isinstance(unread_probe, dict)
            and int(unread_probe.get("__colorless_preflight_missing_room__", -1)) == 0,
            "Supabase unread-count RPC is unavailable or returned an invalid probe response.",
            failures,
        )
        require(isinstance(integrity, dict), "Supabase identity integrity RPC returned an invalid response.", failures)
        if isinstance(integrity, dict):
            for key in (
                "users_without_account",
                "accounts_over_identity_limit",
                "sessions_without_account_identity",
                "sessions_with_foreign_identity",
            ):
                require(int(integrity.get(key, -1)) == 0, f"Supabase integrity check failed: {key}.", failures)
    except (KeyError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as error:
        failures.append(f"Supabase schema validation failed: {error}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate source and production deployment prerequisites.")
    parser.add_argument("--environment", action="store_true", help="Also validate production environment variables.")
    parser.add_argument("--remote", action="store_true", help="Read-only check the configured production Supabase schema and data integrity.")
    args = parser.parse_args()

    if args.remote and not args.environment:
        parser.error("--remote requires --environment")

    failures = validate_source()
    warnings: list[str] = []
    if args.environment:
        environment_failures, warnings = validate_environment()
        failures.extend(environment_failures)
        if args.remote and not environment_failures:
            failures.extend(validate_remote_supabase())

    for warning in warnings:
        print(f"WARNING: {warning}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    scope = "source + environment + remote schema" if args.remote else "source + environment" if args.environment else "source"
    print(f"Deployment preflight OK ({scope})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
