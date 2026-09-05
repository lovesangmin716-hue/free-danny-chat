import assert from "node:assert/strict";
import {
  isOlderRoomSnapshot,
  mergeAuthoritativeRoomSnapshot,
  mergeRealtimeRoomSnapshot,
  readBoundaryIndex,
  unreadAfterMessageCreated,
} from "../frontend/src/app/platform/room-snapshots.js";

const localRoom = {
  id: "room-1",
  kind: "direct",
  name: "Recipient peer",
  peer: { id: "peer-1", username: "peer" },
  viewer_identity_id: "viewer-1",
  viewer_identity: { id: "viewer-1", username: "viewer" },
  revision: 8,
  updated_at: "2026-09-04T12:00:00Z",
  unread_count: 7,
  last_message: { id: "message-8" },
};

assert.equal(
  mergeRealtimeRoomSnapshot(localRoom, { ...localRoom, unread_count: 0 }).unread_count,
  7,
  "an actor/generic realtime summary cannot clear recipient-local unread",
);
assert.equal(
  mergeRealtimeRoomSnapshot(localRoom, { ...localRoom, unread_count: 99 }).unread_count,
  7,
  "an actor/generic realtime summary cannot inflate recipient-local unread",
);
const olderRealtimeRoom = {
  ...localRoom,
  revision: 7,
  updated_at: "2026-09-04T11:59:00Z",
  unread_count: 0,
  last_message: { id: "message-7" },
};
assert.strictEqual(
  mergeRealtimeRoomSnapshot(localRoom, olderRealtimeRoom),
  localRoom,
  "an out-of-order event can increment unread without rewinding the room snapshot",
);
assert.equal(
  unreadAfterMessageCreated(localRoom, { isIncoming: true, messageAdded: true }),
  8,
);
const genericRealtimeRoom = mergeRealtimeRoomSnapshot(localRoom, {
  id: "room-1",
  kind: "direct",
  name: "Direct chat",
  revision: 8,
  unread_count: 0,
});
assert.equal(genericRealtimeRoom.name, "Recipient peer");
assert.deepEqual(genericRealtimeRoom.peer, localRoom.peer);
assert.equal(genericRealtimeRoom.viewer_identity_id, "viewer-1");
assert.equal(
  unreadAfterMessageCreated(localRoom, { isIncoming: true, messageAdded: true }),
  8,
  "a newly applied incoming message increments once",
);
assert.equal(
  unreadAfterMessageCreated(localRoom, { isIncoming: true, messageAdded: false }),
  7,
  "a snapshot-before-event duplicate does not increment",
);
assert.equal(
  unreadAfterMessageCreated(localRoom, { isIncoming: true, messageAdded: true, autoRead: true }),
  0,
  "a message read immediately in the active room remains read",
);
assert.equal(
  unreadAfterMessageCreated(localRoom, { isIncoming: false, messageAdded: true }),
  7,
  "the sender's own event does not change unrelated unread",
);

const staleSnapshot = {
  ...localRoom,
  revision: 7,
  updated_at: "2026-09-04T11:59:00Z",
  unread_count: 6,
  last_message: { id: "message-7" },
};
assert.equal(isOlderRoomSnapshot(localRoom, staleSnapshot), true);
assert.strictEqual(
  mergeAuthoritativeRoomSnapshot(localRoom, staleSnapshot),
  localRoom,
  "a stale reset response cannot rewind a realtime-applied room",
);
assert.equal(
  mergeAuthoritativeRoomSnapshot(localRoom, { ...localRoom, unread_count: 3 }).unread_count,
  3,
  "a current recipient-specific snapshot may correct unread exactly",
);
const justReadRoom = { ...localRoom, unread_count: 0, _local_unread_known: true };
assert.equal(
  mergeAuthoritativeRoomSnapshot(justReadRoom, { ...localRoom, unread_count: 7 }).unread_count,
  0,
  "an equal-revision response started before a local read cannot restore unread",
);
const previouslyUnseen = mergeRealtimeRoomSnapshot(null, localRoom);
assert.equal(
  mergeAuthoritativeRoomSnapshot(previouslyUnseen, { ...localRoom, unread_count: 7 }).unread_count,
  7,
  "the first recipient-specific snapshot corrects an unknown realtime baseline",
);

const messages = [{ id: "M1" }, { id: "M2" }];
const indexes = new Map(messages.map((message, index) => [message.id, index]));
assert.equal(readBoundaryIndex(messages, indexes, "M1"), 0);
assert.equal(
  readBoundaryIndex(messages, indexes, "missing"),
  -1,
  "an unknown/stale cursor must not mark newer messages read",
);
assert.equal(readBoundaryIndex(messages, indexes), 1);

console.log("Room snapshot unread reconciliation passed");
