from __future__ import annotations

import math
import tempfile
import time
import unittest
from pathlib import Path

from colorless.places import DEMO_PLACES, PlaceService
from colorless.state import StateStore


class PlaceServiceTestCase(unittest.TestCase):
    def test_demo_search_returns_signed_place_selection(self) -> None:
        service = PlaceService()
        service.search_enabled = False

        result = service.search("롯데월드")

        self.assertEqual(result["provider"], "demo")
        self.assertEqual(len(result["items"]), 1)
        selected = result["items"][0]
        decoded = service.decode_token(selected["place_token"])
        self.assertEqual(decoded["key"], selected["key"])
        self.assertEqual(decoded["name"], "롯데월드")

        payload, signature = selected["place_token"].split(".", 1)
        tampered = f"{payload}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
        with self.assertRaisesRegex(ValueError, "올바르지 않습니다"):
            service.decode_token(tampered)


class PlaceRoomTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="colorless-place-")
        self.store = StateStore(Path(self.temp_dir.name) / "state.json")
        self.alice = self.store.create_or_update_social_user("demo", "place-alice", nickname="alice")
        self.bob = self.store.create_or_update_social_user("demo", "place-bob", nickname="bob")
        service = PlaceService()
        self.place = service.decode_token(service.issue_token(dict(DEMO_PLACES[0])))

    def tearDown(self) -> None:
        self.assertTrue(self.store.close())
        self.temp_dir.cleanup()

    def test_place_room_is_shared_and_verification_badge_lasts_three_hours(self) -> None:
        room, created, joined, error = self.store.open_place_room("alice", self.place)
        self.assertIsNone(error)
        self.assertTrue(created)
        self.assertTrue(joined)
        assert room is not None

        bob_room, bob_created, bob_joined, error = self.store.open_place_room("bob", self.place)
        self.assertIsNone(error)
        self.assertFalse(bob_created)
        self.assertTrue(bob_joined)
        self.assertEqual(bob_room["id"], room["id"])
        self.assertEqual(bob_room["participant_count"], 2)

        verified_at = time.time()
        verification, error = self.store.verify_place_location(
            "alice",
            room["id"],
            self.place["latitude"],
            self.place["longitude"],
            12.0,
            verified_at,
        )
        self.assertIsNone(error)
        assert verification is not None
        self.assertAlmostEqual(verification["expires_at"] - verification["verified_at"], 3 * 60 * 60, delta=0.1)

        saved_message = self.store.add_message(room["id"], "alice", "현장입니다")
        assert saved_message is not None
        message, _, created_message = saved_message
        self.assertTrue(created_message)
        self.assertEqual(message["place_verification_expires_at"], verification["expires_at"])

        status = self.store.place_verification_status("alice", room["id"])
        self.assertTrue(status["active"])

    def test_place_verification_rejects_remote_stale_and_non_finite_locations(self) -> None:
        room, _, _, _ = self.store.open_place_room("alice", self.place)
        assert room is not None

        for latitude, longitude, accuracy, captured_at in (
            (37.5665, 126.9780, 10.0, time.time()),
            (self.place["latitude"], self.place["longitude"], 10.0, time.time() - 121),
            (self.place["latitude"], self.place["longitude"], 101.0, time.time()),
            (self.place["latitude"], self.place["longitude"], math.nan, time.time()),
        ):
            with self.subTest(latitude=latitude, longitude=longitude, accuracy=accuracy):
                verification, error = self.store.verify_place_location(
                    "alice", room["id"], latitude, longitude, accuracy, captured_at
                )
                self.assertIsNone(verification)
                self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
