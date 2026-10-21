// Memory updates stay with the caller; defer synchronous browser storage I/O.
export function createDraftWriter(storage, bindLifecycle = false) {
  const pending = new Map();
  let timer;
  function flush() {
    clearTimeout(timer); timer = undefined;
    for (const [key, value] of pending) {
      try {
        if (value) storage().setItem(key, value);
        else storage().removeItem(key);
      } catch (_) { /* Caller retains the in-memory draft. */ }
    }
    pending.clear();
  }
  if (bindLifecycle) {
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", () => { if (document.hidden) flush(); });
  }
  return {
    queue(key, value) {
      pending.set(key, value);
      clearTimeout(timer); timer = setTimeout(flush, 200);
    },
    remove(key) {
      pending.delete(key);
      try { storage().removeItem(key); } catch (_) { /* Storage may be unavailable. */ }
    },
    flush,
  };
}
