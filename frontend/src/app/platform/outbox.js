// In-memory, room-scoped optimistic messages. File objects and object URLs are
// intentionally kept out of web storage, but survive navigation between rooms.
export function createTransientOutbox() {
  const rooms = new Map();

  function room(roomId, create = false) {
    let messages = rooms.get(roomId);
    if (!messages && create) {
      messages = new Map();
      rooms.set(roomId, messages);
    }
    return messages;
  }

  function put(roomId, message) {
    if (!roomId || !message?.id) return false;
    room(roomId, true).set(message.id, message);
    return true;
  }

  function replace(roomId, messageId, message) {
    const messages = room(roomId);
    if (!messages?.has(messageId) || !message?.id) return false;
    if (message.id === messageId) messages.set(messageId, message);
    else {
      const entries = [...messages.entries()];
      messages.clear();
      for (const [id, value] of entries) {
        messages.set(id === messageId ? message.id : id, id === messageId ? message : value);
      }
    }
    return true;
  }

  function remove(roomId, messageId) {
    const messages = room(roomId);
    if (!messages) return null;
    const message = messages.get(messageId) || null;
    messages.delete(messageId);
    if (!messages.size) rooms.delete(roomId);
    return message;
  }

  function list(roomId) {
    return [...(room(roomId)?.values() || [])];
  }

  function has(roomId, messageId) {
    return Boolean(room(roomId)?.has(messageId));
  }

  function clearRoom(roomId) {
    const messages = list(roomId);
    rooms.delete(roomId);
    return messages;
  }

  function clear() {
    const messages = [...rooms.values()].flatMap((roomMessages) => [...roomMessages.values()]);
    rooms.clear();
    return messages;
  }

  return Object.freeze({ clear, clearRoom, has, list, put, remove, replace });
}

export function isPersistedOutboxReplacement(existing, incoming) {
  return Boolean(
    (existing?.pending || existing?.failed)
    && existing.id !== incoming?.id
    && existing.client_message_id
    && existing.client_message_id === incoming?.client_message_id
  );
}
