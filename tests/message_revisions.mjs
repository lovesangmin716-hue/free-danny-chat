import assert from "node:assert/strict";
import { createMessageRevisionJournal } from "../frontend/src/app/platform/message-revisions.js";

const journal = createMessageRevisionJournal(2);
journal.snapshot("room-a", { id: "message-a", text: "edited", mutation_revision: 2 });
assert.equal(
  journal.resolve("room-a", { id: "message-a", text: "created", mutation_revision: 0 }).message.text,
  "edited",
  "an update received before create wins",
);

journal.snapshot("room-a", { id: "message-a", text: "stale", mutation_revision: 1 });
assert.equal(
  journal.resolve("room-a", { id: "message-a", text: "created", mutation_revision: 0 }).message.text,
  "edited",
  "older and duplicate deliveries cannot regress the snapshot",
);

journal.tombstone("room-a", "message-a", 3, { id: "room-a", last_message: null });
journal.snapshot("room-a", { id: "message-a", text: "late update", mutation_revision: 2 });
const deleted = journal.resolve("room-a", { id: "message-a", text: "late create", mutation_revision: 0 });
assert.equal(deleted.deleted, true, "a late create cannot revive a deleted message");
assert.equal(deleted.room.last_message, null);

const racing = createMessageRevisionJournal();
const checkpoint = racing.checkpoint();
racing.snapshot("room-race", { id: "message-new", text: "new", timestamp: "2026-01-01" }, 0, true);
assert.deepEqual(
  racing.resolveAll("room-race", [], checkpoint).map((message) => message.id),
  ["message-new"],
  "a create received during a snapshot load is merged into that response",
);
racing.tombstone("room-race", "message-new", 1);
assert.deepEqual(racing.resolveAll("room-race", [{ id: "message-new" }]), []);

journal.snapshot("room-b", { id: "message-b", text: "b", mutation_revision: 1 });
journal.snapshot("room-c", { id: "message-c", text: "c", mutation_revision: 1 });
assert.equal(journal.size(), 2, "the journal is bounded");
assert.equal(journal.resolve("room-a", { id: "message-a", text: "evicted" }).deleted, false);

journal.clear();
assert.equal(journal.size(), 0);
console.log("Message revision journal passed");
