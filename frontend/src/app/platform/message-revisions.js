// Bounded, room-scoped history for message mutations that may arrive before
// their create event or while the message is outside the loaded viewport.
export function createMessageRevisionJournal(limit = 512) {
  const records = new Map();
  let serial = 0;

  function revision(value) {
    const number = Number(value);
    return Number.isSafeInteger(number) && number >= 0 ? number : null;
  }

  function key(roomId, messageId) {
    return roomId && messageId ? `${roomId}\u0000${messageId}` : "";
  }

  function store(recordKey, record) {
    record.serial = ++serial;
    records.delete(recordKey);
    records.set(recordKey, record);
    while (records.size > limit) records.delete(records.keys().next().value);
    return record;
  }

  function snapshot(roomId, message, mutationRevision = null, includeIfMissing = false) {
    const recordKey = key(roomId, message?.id);
    if (!recordKey) return null;
    const existing = records.get(recordKey);
    if (existing?.deleted) return existing;
    const incomingRevision = revision(mutationRevision ?? message.mutation_revision);
    if (
      existing && existing.revision !== null
      && incomingRevision !== null
      && incomingRevision < existing.revision
    ) return includeIfMissing
      ? store(recordKey, { ...existing, includeIfMissing: true })
      : existing;
    return store(recordKey, {
      deleted: false,
      revision: incomingRevision ?? existing?.revision ?? null,
      message: { ...(existing?.message || {}), ...message },
      room: null,
      roomId,
      includeIfMissing: includeIfMissing || Boolean(existing?.includeIfMissing),
    });
  }

  function tombstone(roomId, messageId, mutationRevision = null, room = null) {
    const recordKey = key(roomId, messageId);
    if (!recordKey) return null;
    const existing = records.get(recordKey);
    const incomingRevision = revision(mutationRevision);
    if (
      existing && existing.revision !== null
      && incomingRevision !== null
      && incomingRevision < existing.revision
    ) return existing;
    return store(recordKey, {
      deleted: true,
      revision: incomingRevision ?? existing?.revision ?? null,
      message: null,
      room: room ? { ...(existing?.room || {}), ...room } : (existing?.room || null),
      roomId,
    });
  }

  function resolve(roomId, message) {
    const existing = records.get(key(roomId, message?.id));
    if (!existing) return { deleted: false, message, room: null };
    if (existing.deleted) return { deleted: true, message: null, room: existing.room };
    const messageRevision = revision(message?.mutation_revision);
    if (
      existing.revision !== null
      && messageRevision !== null
      && messageRevision > existing.revision
    ) {
      snapshot(roomId, message, messageRevision);
      return { deleted: false, message, room: null };
    }
    return { deleted: false, message: { ...message, ...existing.message }, room: null };
  }

  function resolveAll(roomId, messages, changedAfter = null) {
    const resolved = [];
    const ids = new Set();
    for (const message of messages || []) {
      const outcome = resolve(roomId, message);
      if (!outcome.deleted && outcome.message) {
        resolved.push(outcome.message);
        ids.add(outcome.message.id);
      }
    }
    if (changedAfter !== null) {
      for (const record of records.values()) {
        if (record.roomId === roomId && record.serial > changedAfter && record.includeIfMissing
          && !record.deleted && !ids.has(record.message?.id)) resolved.push(record.message);
      }
      resolved.sort((left, right) => String(left.timestamp || "").localeCompare(String(right.timestamp || "")));
    }
    return resolved;
  }

  return Object.freeze({
    checkpoint: () => serial,
    clear: () => records.clear(),
    resolve,
    resolveAll,
    size: () => records.size,
    snapshot,
    tombstone,
  });
}
