import json
import time
import unittest

try:
    import test_server as support
except ImportError:
    from tests import test_server as support
from colorless.identity import resolve_actor, event_for_viewer

server = support.server


class IdentityBoundaryTest(unittest.TestCase):
    setUp = support.AttachmentTransferIntegrationTestCase.setUp
    tearDown = support.AttachmentTransferIntegrationTestCase.tearDown
    request = support.AttachmentTransferIntegrationTestCase.request

    def post(self, path, payload, identity=""):
        headers = {"Content-Type": "application/json"}
        if identity:
            headers["X-Acting-Identity"] = identity
        status, response_headers, body = self.request("POST", path, json.dumps(payload).encode(), headers)
        return status, json.loads(body), response_headers

    def new_identity(self):
        suffix = str(time.time_ns())[-12:]
        status, body, _ = self.post("/identities", {"username": f"alt{suffix}", "displayName": "Another ID"})
        self.assertEqual(status, 201, body)
        return body["identity"]

    def test_other_account_cannot_be_an_actor_via_header_or_body(self):
        for headers, payload in ((self.peer["id"], {}), ("", {"acting_identity_id": self.peer["id"]})):
            status, _, _ = self.post("/profile", {"displayName": "Impersonated", **payload}, headers)
            self.assertEqual(status, 403)
        self.assertNotEqual(server.STORE.get_user_public(self.peer["username"])["display_name"], "Impersonated")

    def test_pinned_actor_survives_shared_session_switch_and_cannot_switch_room_sender(self):
        alternate = self.new_identity()
        status, _, _ = self.post("/identities/switch", {"identityId": alternate["id"]})
        self.assertEqual(status, 200)
        status, sent, _ = self.post("/messages", {"roomId": self.room["id"], "text": "Pinned sender"}, self.user["id"])
        self.assertEqual(status, 201, sent)
        self.assertEqual(sent["username"], self.username)
        self.assertEqual(sent["sender_identity_id"], self.user["id"])
        status, _, _ = self.post("/messages", {"roomId": self.room["id"], "text": "Wrong room identity"}, alternate["id"])
        self.assertEqual(status, 403)
        self.assertEqual(server.SESSIONS.get_username(self.session_token), alternate["username"])
        status, _, body = self.request("GET", f"/messages?room_id={self.room['id']}&limit=30",
                                      headers={"X-Acting-Identity": self.user["id"]})
        self.assertEqual(status, 200, body)
        status, _, _ = self.request("GET", f"/messages?room_id={self.room['id']}&limit=30",
                                   headers={"X-Acting-Identity": alternate["id"]})
        self.assertEqual(status, 403)

    def test_disable_releases_capacity_preserves_history_and_revokes_cached_identity(self):
        alternate = self.new_identity()
        self.new_identity()
        status, _, _ = self.post("/identities", {"username": "too_many_ids", "displayName": "Too many"})
        self.assertEqual(status, 400)
        server.STORE.add_friend(alternate["username"], self.peer["id"])
        room, _, _ = server.STORE.create_or_get_direct_room(alternate["username"], self.peer["id"])
        sent = server.STORE.add_message(room["id"], alternate["username"], "Retained history")[0]
        alt_token = server.SESSIONS.create(alternate["username"])
        self.assertEqual(server.SESSIONS.get_username(alt_token), alternate["username"])
        status, body, _ = self.post("/identities/disable", {"identityId": alternate["id"]})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["disabled"])
        self.assertEqual(server.SESSIONS.get_username(alt_token), self.username)
        self.assertIsNone(resolve_actor(server.STORE, self.username, alternate["id"]))
        status, _, _ = self.post("/messages", {"roomId": room["id"], "text": "Must fail"}, alternate["id"])
        self.assertEqual(status, 403)
        self.assertEqual(server.STORE.repository.message_by_id(room["id"], sent["id"])["text"], "Retained history")
        self.new_identity()
        self.assertEqual(len(server.STORE.get_account_context(self.username)["identities"]), 3)

    def test_cannot_disable_current_or_another_accounts_identity(self):
        self.assertEqual(self.post("/identities/disable", {"identityId": self.user["id"]})[0], 409)
        self.assertEqual(self.post("/identities/disable", {"identityId": self.peer["id"]})[0], 403)

    def test_public_profiles_do_not_link_identities_to_authentication(self):
        alternate = self.new_identity()
        for public in (self.user, alternate, server.STORE.get_user_public(self.username)):
            for private in ("account_id", "phone_masked", "auth_provider", "auth_provider_label", "provider_user_id"):
                self.assertNotIn(private, public)
        owner_context = server.STORE.get_account_context(self.username)
        self.assertIn("phone_masked", owner_context["account"])
        event = {"type": "message_created", "roomId": self.room["id"], "message": {"username": self.username}}
        peer_copy = event_for_viewer(server.STORE, self.peer["username"], event)
        self.assertEqual(peer_copy["recipient_identity_ids"], [self.peer["id"]])
        self.assertNotIn(alternate["id"], json.dumps(peer_copy))
        self.assertNotIn("recipient_identity_ids", event)

    def test_suspended_account_cannot_use_cached_session_to_write(self):
        self.assertEqual(server.SESSIONS.get_username(self.session_token), self.username)
        record = server.STORE.get_user_record(self.username)
        with server.STORE.repository.connection() as database:
            database.execute("UPDATE accounts SET status='suspended' WHERE id=?", (record["account_id"],))
        status, _, _ = self.post("/messages", {"roomId": self.room["id"], "text": "Forbidden"}, self.user["id"])
        self.assertEqual(status, 403)

    def test_unread_summary_counts_each_owned_identity_without_peer_linkage(self):
        alternate = self.new_identity()
        server.STORE.add_friend(alternate["username"], self.peer["id"])
        room, _, _ = server.STORE.create_or_get_direct_room(alternate["username"], self.peer["id"])
        server.STORE.add_message(self.room["id"], self.peer["username"], "For primary")
        server.STORE.add_message(room["id"], self.peer["username"], "For alternate")
        status, _, body = self.request("GET", "/identities/unread")
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(result["counts"], {self.user["id"]: 1, alternate["id"]: 1})
        self.assertEqual(result["total"], 2)


if __name__ == "__main__":
    unittest.main()
