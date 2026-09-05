from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("deploy_preflight.py")
SPEC = importlib.util.spec_from_file_location("colorless_deploy_preflight", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
deploy_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy_preflight)


class DeploymentPreflightTestCase(unittest.TestCase):
    def test_environment_warns_when_kakao_login_would_be_disabled(self) -> None:
        environment = {
            "REQUIRE_SUPABASE": "true",
            "PUBLIC_BASE_URL": "https://chat.example.com",
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role-key-long-enough",
            "SOCIAL_DEMO_LOGIN_ENABLED": "false",
            "LOCAL_SIGNUP_ENABLED": "false",
            "PHONE_VERIFICATION_MODE": "prod",
            "GOOGLE_CLIENT_ID": "google-client",
            "GOOGLE_CLIENT_SECRET": "google-secret",
        }
        with mock.patch.dict(deploy_preflight.os.environ, environment, clear=True):
            failures, warnings = deploy_preflight.validate_environment()

        self.assertEqual(failures, [])
        self.assertIn("KAKAO_REST_API_KEY is missing; Kakao login will be disabled.", warnings)

    def test_environment_warns_when_kakao_client_secret_may_be_required(self) -> None:
        environment = {
            "REQUIRE_SUPABASE": "true",
            "PUBLIC_BASE_URL": "https://chat.example.com",
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role-key-long-enough",
            "SOCIAL_DEMO_LOGIN_ENABLED": "false",
            "LOCAL_SIGNUP_ENABLED": "false",
            "PHONE_VERIFICATION_MODE": "prod",
            "GOOGLE_CLIENT_ID": "google-client",
            "GOOGLE_CLIENT_SECRET": "google-secret",
            "KAKAO_REST_API_KEY": "kakao-key",
        }
        with mock.patch.dict(deploy_preflight.os.environ, environment, clear=True):
            failures, warnings = deploy_preflight.validate_environment()

        self.assertEqual(failures, [])
        self.assertIn(
            "KAKAO_CLIENT_SECRET is missing; token issuance fails unless Client Secret is disabled in Kakao Developers.",
            warnings,
        )

    def test_remote_schema_integrity_accepts_a_migrated_database(self) -> None:
        responses = [
            [{"id": "account-1"}],
            [{"id": "user-1", "account_id": "account-1"}],
            [{"token_hash": "hash", "account_id": "account-1", "active_user_id": "user-1"}],
            [],
            {"error": "not_found"},
            {"error": "not_found"},
            {"error": "not_found"},
            {"error": "not_found"},
            {"__colorless_preflight_missing_room__": 0},
            {
                "users_without_account": 0,
                "accounts_over_identity_limit": 0,
                "sessions_without_account_identity": 0,
                "sessions_with_foreign_identity": 0,
            },
            {"error": "forbidden"},
        ]
        with mock.patch.object(deploy_preflight, "supabase_request", side_effect=responses) as request:
            self.assertEqual(deploy_preflight.validate_remote_supabase(), [])
        self.assertEqual(request.call_count, 11)
        self.assertEqual(
            request.call_args_list[4].args[0],
            "/rest/v1/rpc/colorless_insert_message_v2",
        )
        self.assertEqual(
            request.call_args_list[5].args[0],
            "/rest/v1/rpc/colorless_edit_message",
        )
        self.assertEqual(
            request.call_args_list[6].args[0],
            "/rest/v1/rpc/colorless_toggle_message_reaction",
        )
        self.assertEqual(
            request.call_args_list[7].args[0],
            "/rest/v1/rpc/colorless_delete_message",
        )
        self.assertEqual(
            request.call_args_list[8].args[0],
            "/rest/v1/rpc/colorless_unread_counts",
        )

    def test_remote_schema_integrity_blocks_invalid_sessions(self) -> None:
        responses = [
            [],
            [],
            [],
            [],
            {"error": "not_found"},
            {"error": "not_found"},
            {"error": "not_found"},
            {"error": "not_found"},
            {"__colorless_preflight_missing_room__": 0},
            {
                "users_without_account": 0,
                "accounts_over_identity_limit": 0,
                "sessions_without_account_identity": 1,
                "sessions_with_foreign_identity": 0,
            },
            {"error": "forbidden"},
        ]
        with mock.patch.object(deploy_preflight, "supabase_request", side_effect=responses):
            failures = deploy_preflight.validate_remote_supabase()
        self.assertIn("Supabase integrity check failed: sessions_without_account_identity.", failures)

    def test_remote_schema_failure_does_not_include_credentials(self) -> None:
        with mock.patch.object(
            deploy_preflight,
            "supabase_request",
            side_effect=RuntimeError("GET /rest/v1/accounts returned HTTP 404"),
        ):
            failures = deploy_preflight.validate_remote_supabase()
        self.assertEqual(
            failures,
            ["Supabase schema validation failed: GET /rest/v1/accounts returned HTTP 404"],
        )


if __name__ == "__main__":
    unittest.main()
