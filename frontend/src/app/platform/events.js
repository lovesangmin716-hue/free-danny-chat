// Typed realtime event router. Feature handlers register independently.
export function createEventRouter({ onUnknown, onError } = {}) {
    const handlers = new Map();
    // One router receives both EventSource pushes and /sync replays. Keeping the
    // bounded event-id cache here makes deduplication shared across transports.
    const seenEventIds = new Set();
    const inFlightEventIds = new Map();

    function eventId(event) {
      return String(event?.event_id || "");
    }

    function rememberEvent(event) {
      const id = eventId(event);
      if (!id) return;
      seenEventIds.add(id);
      if (seenEventIds.size > 1000) seenEventIds.delete(seenEventIds.values().next().value);
    }

    function register(type, handler) {
      if (!type || typeof handler !== "function") throw new TypeError("event registration requires type and handler");
      const listeners = handlers.get(type) || new Set();
      listeners.add(handler);
      handlers.set(type, listeners);
      return () => listeners.delete(handler);
    }

    async function dispatchOnce(event, context) {
      const type = event?.type;
      const listeners = handlers.get(type);
      if (!listeners?.size) {
        if (typeof onUnknown === "function") return onUnknown(event, context);
        return false;
      }
      for (const handler of [...listeners]) {
        try {
          await handler(event, context);
        } catch (error) {
          if (typeof onError === "function") onError(error, event);
          throw error;
        }
      }
      return true;
    }

    async function dispatch(event, context = {}) {
      const id = eventId(event);
      if (id && seenEventIds.has(id)) return true;
      if (id && inFlightEventIds.has(id)) return inFlightEventIds.get(id);
      const task = dispatchOnce(event, context).then((handled) => {
        if (handled) rememberEvent(event);
        return handled;
      });
      if (id) inFlightEventIds.set(id, task);
      try {
        return await task;
      } finally {
        if (id && inFlightEventIds.get(id) === task) inFlightEventIds.delete(id);
      }
    }

    return Object.freeze({ register, dispatch, has: type => handlers.has(type), markHandled: rememberEvent });
  }

export function createRealtimeClient({ url, router, context, onOpen, onUnhandled, onError } = {}) {
    if (!url || !router) throw new TypeError("realtime client requires url and router");
    let source = null;
    const cursorStorageKey = "colorless-realtime-cursor";

    function close() {
      source?.close();
      source = null;
    }

    function open() {
      if (source) return;
      const eventUrl = new URL(url, window.location.href);
      const savedCursor = window.sessionStorage.getItem(cursorStorageKey) || "";
      if (/^\d+$/.test(savedCursor) && savedCursor !== "0") eventUrl.searchParams.set("after", savedCursor);
      source = new EventSource(`${eventUrl.pathname}${eventUrl.search}`);
      source.onopen = () => onOpen?.();
      source.onmessage = async (message) => {
        let payload;
        try {
          payload = JSON.parse(message.data);
        } catch (_) {
          return;
        }
        const eventContext = typeof context === "function" ? context() : (context || {});
        try {
          const handled = await router.dispatch(payload, eventContext);
          if (!handled) {
            await onUnhandled?.(payload, eventContext);
            router.markHandled?.(payload);
          }
          if (/^\d+$/.test(message.lastEventId || "")) {
            window.sessionStorage.setItem(cursorStorageKey, message.lastEventId);
          }
        } catch (error) {
          onError?.(error);
        }
      };
      source.onerror = (error) => onError?.(error);
    }

    return Object.freeze({ open, close, isOpen: () => Boolean(source) });
}
