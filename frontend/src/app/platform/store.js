// Observable application state with explicit, named transactions.
export function createStore(initialState) {
    if (!initialState || typeof initialState !== "object") {
      throw new TypeError("createStore requires an object state");
    }

    const listeners = new Set();
    const state = initialState;
    let version = 0;

    function notify(change) {
      for (const listener of [...listeners]) {
        try {
          listener(change, state);
        } catch (error) {
          queueMicrotask(() => { throw error; });
        }
      }
    }

    function transact(type, mutate, metadata = {}) {
      if (typeof mutate !== "function") throw new TypeError("store transaction requires a mutation function");
      const result = mutate(state);
      version += 1;
      notify(Object.freeze({ type, version, metadata, timestamp: Date.now() }));
      return result;
    }

    function touch(type, metadata = {}) {
      version += 1;
      notify(Object.freeze({ type, version, metadata, timestamp: Date.now() }));
    }

    function subscribe(listener) {
      if (typeof listener !== "function") throw new TypeError("store subscriber must be a function");
      listeners.add(listener);
      return () => listeners.delete(listener);
    }

    return Object.freeze({
      state,
      transact,
      touch,
      subscribe,
      getVersion: () => version,
    });
}
