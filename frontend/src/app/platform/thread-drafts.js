export function draftKey(identity, parent = "") { return `colorless:thread:${identity}:${parent}`; }
const fallback = new Map();
export function loadDraft(storage, identity, parent = "") {
  const key = draftKey(identity, parent);
  try {
    const value = fallback.has(key) ? fallback.get(key) : JSON.parse(storage.getItem(key) || "null");
    return value && typeof value.body === "string" && typeof value.clientId === "string" && /^[\w-]{1,100}$/.test(value.clientId)
      ? value : { body: "", clientId: crypto.randomUUID() };
  }
  catch (_) { return { body: "", clientId: crypto.randomUUID() }; }
}
export function saveDraft(storage, identity, parent, draft) {
  const key = draftKey(identity, parent);
  try { storage.setItem(key, JSON.stringify(draft)); fallback.delete(key); }
  catch (_) { fallback.set(key, { ...draft }); }
}
export function clearDraft(storage, identity, parent) {
  const key = draftKey(identity, parent);
  try { storage.removeItem(key); fallback.delete(key); }
  catch (_) { fallback.set(key, null); }
}

export function finishDraft(storage, identity, parent, sent) {
  const latest = loadDraft(storage, identity, parent);
  if (latest.clientId !== sent.clientId) return;
  if (latest.body === sent.body && latest.visibility === sent.visibility) clearDraft(storage, identity, parent);
  else saveDraft(storage, identity, parent, { ...latest, clientId: crypto.randomUUID() });
}
