import assert from "node:assert/strict";
import { withActor, sessionForWindow, unreadByIdentity } from "../frontend/src/app/platform/identity-context.js";

globalThis.window = { location: { href: "https://chat.test/", origin: "https://chat.test", search: "" } };
const session = { user: { id: "one" }, active_identity_id: "one", identities: [{ id: "one" }, { id: "two" }] };
const state = { session, selectedRoomId: "room", roomById: new Map([["room", { viewer_identity_id: "two" }]]) };
const request = withActor(state, "/messages", { method: "POST", body: JSON.stringify({ roomId: "room" }) });
assert.equal(request.headers.get("X-Acting-Identity"), "two");
assert.equal(withActor(state, "/messages?room_id=room&limit=30").headers.get("X-Acting-Identity"), "two");
state.session = { ...session, active_identity_id: "other-tab" };
assert.equal(request.headers.get("X-Acting-Identity"), "two", "Captured requests must not change actors");
assert.equal(withActor(state, "/uploads/complete", { headers: { "X-Acting-Identity": "one" } }).headers.get("X-Acting-Identity"), "one");
assert.equal(withActor(state, "https://storage.test/upload", {}).headers, undefined, "Do not disclose identities to storage providers");
assert.equal(sessionForWindow(session, "?identity=two").active_identity_id, "two");
assert.equal(sessionForWindow(session, "?identity=outsider"), session);
assert.deepEqual([...unreadByIdentity([{ viewer_identity_id: "one", unread_count: 2 }, { viewer_identity_id: "one", unread_count: 3 }, { viewer_identity_id: "two", unread_count: 4 }])], [["one", 5], ["two", 4]]);
console.log("Identity request isolation passed");
