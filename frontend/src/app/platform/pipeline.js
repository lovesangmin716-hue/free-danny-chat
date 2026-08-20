// Shared lifecycle for every feature action: execute, commit, effect, and failure.
export function createActionPipeline({ store, context = {}, onError } = {}) {
    if (!store) throw new TypeError("action pipeline requires a store");
    const observers = new Set();
    const activeByKey = new Map();

    function observe(listener) {
      observers.add(listener);
      return () => observers.delete(listener);
    }

    function emit(phase, name, detail = {}) {
      const event = Object.freeze({ phase, name, timestamp: Date.now(), ...detail });
      for (const observer of [...observers]) observer(event);
      return event;
    }

    async function run(name, input, definition) {
      if (!definition || typeof definition.execute !== "function") {
        throw new TypeError(`action ${name} requires execute()`);
      }
      const key = typeof definition.key === "function" ? definition.key(input) : definition.key;
      if (key && definition.policy === "join" && activeByKey.has(key)) return activeByKey.get(key);

      const task = (async () => {
        emit("start", name, { input });
        try {
          const result = await definition.execute({ ...context, store, input });
          let committed;
          if (typeof definition.commit === "function") {
            committed = store.transact(name, state => definition.commit(state, result, input), { source: "action" });
          } else {
            store.touch(name, { source: "action", changed: false });
          }
          if (typeof definition.effect === "function") {
            await definition.effect({ ...context, store, input, result, committed });
          }
          emit("success", name, { input, result });
          return result;
        } catch (error) {
          emit("error", name, { input, error });
          if (typeof definition.failure === "function") {
            await definition.failure({ ...context, store, input, error });
          } else if (typeof onError === "function") {
            onError(error, { name, input });
          }
          throw error;
        } finally {
          emit("settled", name, { input });
        }
      })();

      if (key) activeByKey.set(key, task);
      try {
        return await task;
      } finally {
        if (key && activeByKey.get(key) === task) activeByKey.delete(key);
      }
    }

    return Object.freeze({ run, observe, isRunning: key => activeByKey.has(key) });
}
