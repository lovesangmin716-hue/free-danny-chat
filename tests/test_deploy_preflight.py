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
    def test_remote_schema_integrity_accepts_a_migrated_database(self) -> None:
        responses = [
            [{"id": "account-1"}],
            [{"id": "user-1", "account_id": "account-1"}],
            [{"token_hash": "hash", "account_id": "account-1", "active_user_id": "user-1"}],
            {
                "users_without_account": 0,
                "accounts_over_identity_limit": 0,
                "sessions_without_account_identity": 0,
                "sessions_with_foreign_identity": 0,
            },
        ]
        with mock.patch.object(deploy_preflight, "supabase_request", side_effect=responses) as request:
            self.assertEqual(deploy_preflight.validate_remote_supabase(), [])
        self.assertEqual(request.call_count, 4)

    def test_remote_schema_integrity_blocks_invalid_sessions(self) -> None:
        responses = [
            [],
            [],
            [],
            {
                "users_without_account": 0,
                "accounts_over_identity_limit": 0,
                "sessions_without_account_identity": 1,
                "sessions_with_foreign_identity": 0,
            },
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
