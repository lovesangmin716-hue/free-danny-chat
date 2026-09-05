import assert from "node:assert/strict";

import {
  createTransientOutbox,
  isPersistedOutboxReplacement,
} from "../frontend/src/app/platform/outbox.js";

const outbox = createTransientOutbox();
const pending = {
  id: "pending-1",
  pending: true,
  client_message_id: "client-1",
  retry_data: { clientMessageId: "client-1" },
};
const failed = { ...pending, pending: false, failed: true };
const later = { id: "pending-later" };

assert.equal(outbox.put("room-a", pending), true);
assert.equal(outbox.put("room-a", later), true);
assert.equal(outbox.put("room-b", { id: "pending-2" }), true);
assert.deepEqual(outbox.list("room-a"), [pending, later]);
assert.equal(outbox.replace("room-a", pending.id, failed), true);
assert.deepEqual(outbox.list("room-a"), [failed, later], "status updates preserve send order");
assert.equal(outbox.has("room-a", pending.id), true);
assert.equal(outbox.remove("room-a", pending.id), failed);
assert.deepEqual(outbox.list("room-a"), [later]);
assert.equal(outbox.remove("room-a", later.id), later);
assert.equal(outbox.clear().length, 1);

const replyReferenceUpdate = {
  ...pending,
  reply_to: { id: "source-1", deleted: true },
};
assert.equal(
  isPersistedOutboxReplacement(pending, replyReferenceUpdate),
  false,
  "a local reply-reference refresh must not acknowledge a pending send",
);
assert.equal(
  isPersistedOutboxReplacement(failed, { ...replyReferenceUpdate, pending: false, failed: true }),
  false,
  "a local reply-reference refresh must keep a failed send retryable",
);
assert.equal(
  isPersistedOutboxReplacement(pending, {
    id: "message-1",
    client_message_id: pending.retry_data.clientMessageId,
  }),
  true,
  "only a distinct persisted message id acknowledges the optimistic send",
);

console.log("Transient room outbox passed");
