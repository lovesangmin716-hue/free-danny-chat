export function draftKey(identity, parent = "") { return `colorless:thread:${identity}:${parent}`; }
export function loadDraft(storage, identity, parent = "") {
  try {
    const value = JSON.parse(storage.getItem(draftKey(identity, parent)) || "null");
    return value && typeof value.body === "string" && typeof value.clientId === "string" && /^[\w-]{1,100}$/.test(value.clientId)
      ? value : { body: "", clientId: crypto.randomUUID() };
  }
  catch (_) { return { body: "", clientId: crypto.randomUUID() }; }
}
export function saveDraft(storage, identity, parent, draft) {
  try { storage.setItem(draftKey(identity, parent), JSON.stringify(draft)); } catch (_) { /* The editor remains usable without storage. */ }
}
export function clearDraft(storage, identity, parent) {
  try { storage.removeItem(draftKey(identity, parent)); } catch (_) { /* Storage may be disabled. */ }
}

export function finishDraft(storage, identity, parent, sent) {
  const latest = loadDraft(storage, identity, parent);
  if (latest.clientId !== sent.clientId) return;
  if (latest.body === sent.body && latest.visibility === sent.visibility) clearDraft(storage, identity, parent);
  else saveDraft(storage, identity, parent, { ...latest, clientId: crypto.randomUUID() });
}
