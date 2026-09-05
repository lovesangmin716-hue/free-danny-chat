import assert from "node:assert/strict";

import { createMessageSubmitGuard } from "../frontend/src/app/platform/submit-guard.js";

let sequence = 0;
const guard = createMessageSubmitGuard(() => `client-${++sequence}`);
const original = { authEpoch: 4, identityId: "alice", roomId: "room-1", text: "hello" };
const first = guard.reserve(original);
const responseLossRetry = guard.reserve({ ...original });
assert.equal(responseLossRetry.clientMessageId, first.clientMessageId);

const changedText = guard.reserve({ ...original, text: "hello again" });
assert.notEqual(changedText.clientMessageId, first.clientMessageId);
guard.confirm(first);
assert.equal(
  guard.reserve({ ...original, text: "hello again" }).clientMessageId,
  changedText.clientMessageId,
  "a stale confirmation cannot clear the newer submission",
);
guard.confirm(changedText);
assert.notEqual(guard.reserve({ ...original, text: "hello again" }).clientMessageId, changedText.clientMessageId);

guard.clear();
const nextSession = guard.reserve({ ...original, authEpoch: 5 });
assert.equal(nextSession.clientMessageId, "client-4");

console.log("Idempotent submit guard passed");
