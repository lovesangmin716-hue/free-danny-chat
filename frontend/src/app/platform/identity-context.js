// Capture the actor before async work; a different tab may switch the shared session.
export function withActor(state, url, options = {}) {
  if (!state.session?.user) return options;
  const parsed = new URL(url, window.location.href);
  if (parsed.origin !== window.location.origin || /^\/(?:auth\/|login$|logout$|session$|identities)/.test(parsed.pathname)) return options;
  let body = {};
  if (typeof options.body === "string") {
    try { body = JSON.parse(options.body); } catch (_) { /* Transport handles malformed JSON. */ }
  }
  const roomId = body.roomId || body.activeRoomId || parsed.searchParams.get("roomId") || parsed.searchParams.get("room_id")
    || (parsed.pathname.startsWith("/uploads") ? state.selectedRoomId : "");
  const room = state.roomById.get(roomId);
  const identity = body.acting_identity_id || room?.viewer_identity_id || state.session.active_identity_id;
  if (!identity) return options;
  const headers = new Headers(options.headers || {});
  if (!headers.has("X-Acting-Identity")) headers.set("X-Acting-Identity", identity);
  return { ...options, headers };
}

export function sessionForWindow(session, search = window.location.search) {
  const identityId = new URLSearchParams(search).get("identity");
  const user = session.identities?.find((identity) => identity.id === identityId);
  return user ? { ...session, user, active_identity_id: user.id } : session;
}

export function unreadByIdentity(rooms) {
  const counts = new Map();
  for (const room of rooms) {
    const id = room.viewer_identity_id;
    if (id) counts.set(id, (counts.get(id) || 0) + Math.max(0, Number(room.unread_count) || 0));
  }
  return counts;
}
