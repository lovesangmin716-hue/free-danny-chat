// Retain an idempotency key until a mutation receives a confirmed response.
export function createMessageSubmitGuard(createClientMessageId) {
  if (typeof createClientMessageId !== "function") {
    throw new TypeError("message submit guard requires an id factory");
  }
  let pending = null;

  function reserve({ authEpoch, identityId, roomId, text }) {
    const key = JSON.stringify([
      Number(authEpoch || 0),
      String(identityId || ""),
      String(roomId || ""),
      String(text || ""),
    ]);
    if (!pending || pending.key !== key) {
      pending = Object.freeze({ key, clientMessageId: createClientMessageId() });
    }
    return pending;
  }

  function confirm(submission) {
    if (pending === submission) pending = null;
  }

  function clear() {
    pending = null;
  }

  return Object.freeze({ clear, confirm, reserve });
}
