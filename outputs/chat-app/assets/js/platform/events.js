// Typed realtime event router. Feature handlers register independently.
(function registerEvents(global) {
  "use strict";

  const platform = global.ColorlessPlatform || (global.ColorlessPlatform = {});

  function createEventRouter({ onUnknown, onError } = {}) {
    const handlers = new Map();

    function register(type, handler) {
      if (!type || typeof handler !== "function") throw new TypeError("event registration requires type and handler");
      const listeners = handlers.get(type) || new Set();
      listeners.add(handler);
      handlers.set(type, listeners);
      return () => listeners.delete(handler);
    }

    async function dispatch(event, context = {}) {
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
          else throw error;
        }
      }
      return true;
    }

    return Object.freeze({ register, dispatch, has: type => handlers.has(type) });
  }

  function createRealtimeClient({ url, router, context, onOpen, onUnhandled, onError } = {}) {
    if (!url || !router) throw new TypeError("realtime client requires url and router");
    let source = null;
    const seenEventIds = new Set();
    const cursorStorageKey = "colorless-realtime-cursor";

    function close() {
      source?.close();
      source = null;
    }

    function open() {
      if (source) return;
      const eventUrl = new URL(url, global.location.href);
      const savedCursor = global.sessionStorage.getItem(cursorStorageKey) || "";
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
        if (/^\d+$/.test(message.lastEventId || "")) {
          global.sessionStorage.setItem(cursorStorageKey, message.lastEventId);
        }
        if (payload?.event_id && seenEventIds.has(payload.event_id)) return;
        if (payload?.event_id) {
          seenEventIds.add(payload.event_id);
          if (seenEventIds.size > 1000) seenEventIds.delete(seenEventIds.values().next().value);
        }
        const eventContext = typeof context === "function" ? context() : (context || {});
        const handled = await router.dispatch(payload, eventContext);
        if (!handled) await onUnhandled?.(payload, eventContext);
      };
      source.onerror = (error) => onError?.(error);
    }

    return Object.freeze({ open, close, isOpen: () => Boolean(source) });
  }

  platform.createEventRouter = createEventRouter;
  platform.createRealtimeClient = createRealtimeClient;
})(window);
