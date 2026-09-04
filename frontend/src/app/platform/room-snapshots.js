function unreadCount(value) {
  const count = Number(value);
  return Number.isSafeInteger(count) && count > 0 ? count : 0;
}

function roomRevision(room) {
  const revision = Number(room?.revision);
  return Number.isSafeInteger(revision) && revision >= 0 ? revision : null;
}

export function isOlderRoomSnapshot(existingRoom, incomingRoom) {
  if (!existingRoom?.id || existingRoom.id !== incomingRoom?.id) return false;
  const existingRevision = roomRevision(existingRoom);
  const incomingRevision = roomRevision(incomingRoom);
  if (existingRevision !== null && incomingRevision !== null) {
    return incomingRevision < existingRevision;
  }
  const existingTime = Date.parse(existingRoom.updated_at || "");
  const incomingTime = Date.parse(incomingRoom.updated_at || "");
  return Number.isFinite(existingTime)
    && Number.isFinite(incomingTime)
    && incomingTime < existingTime;
}

// /rooms is recipient-specific and authoritative, except when an older request
// returns after a newer realtime room revision has already been applied.
export function mergeAuthoritativeRoomSnapshot(existingRoom, incomingRoom) {
  if (!incomingRoom?.id) return incomingRoom;
  if (isOlderRoomSnapshot(existingRoom, incomingRoom)) return existingRoom;
  const sameRevision = roomRevision(existingRoom) !== null
    && roomRevision(existingRoom) === roomRevision(incomingRoom);
  const incomingUnread = unreadCount(incomingRoom.unread_count);
  const resolvedUnread = sameRevision && existingRoom?._local_unread_known !== false
    ? Math.min(unreadCount(existingRoom.unread_count), incomingUnread)
    : incomingUnread;
  return {
    ...(existingRoom || {}),
    ...incomingRoom,
    unread_count: resolvedUnread,
    _local_unread_known: true,
  };
}

// Realtime room summaries are created without a recipient/viewer and therefore
// must never replace recipient-local unread or identity fields.
export function mergeRealtimeRoomSnapshot(existingRoom, incomingRoom) {
  if (!incomingRoom?.id) return incomingRoom;
  if (isOlderRoomSnapshot(existingRoom, incomingRoom)) return existingRoom;
  const directRoom = (incomingRoom.kind || existingRoom?.kind) === "direct";
  return {
    ...(existingRoom || {}),
    ...incomingRoom,
    ...(directRoom && existingRoom ? {
      name: existingRoom.name,
      peer: existingRoom.peer,
    } : {}),
    ...(existingRoom?.viewer_identity_id ? {
      viewer_identity_id: existingRoom.viewer_identity_id,
      viewer_identity: existingRoom.viewer_identity,
    } : {}),
    unread_count: existingRoom
      ? unreadCount(existingRoom.unread_count)
      : unreadCount(incomingRoom.unread_count),
    _local_unread_known: existingRoom?._local_unread_known !== false && Boolean(existingRoom),
  };
}

export function unreadAfterMessageCreated(
  existingRoom,
  { isIncoming = false, messageAdded = false, autoRead = false } = {},
) {
  const unread = unreadCount(existingRoom?.unread_count);
  if (!isIncoming || !messageAdded) return unread;
  return autoRead ? 0 : unread + 1;
}

export function readBoundaryIndex(messages, messageIndexes, lastReadMessageId = "") {
  if (!lastReadMessageId) return messages.length - 1;
  const index = messageIndexes.get(lastReadMessageId);
  return Number.isInteger(index) ? index : -1;
}
