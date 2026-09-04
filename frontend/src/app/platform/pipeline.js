// Shared lifecycle for every feature action: execute, commit, effect, and failure.
function cancelledAction(name) {
  const error = new Error(`Action ${name} was replaced`);
  error.name = "AbortError";
  return error;
}

export function createActionPipeline({ store, context = {}, onError } = {}) {
  if (!store) throw new TypeError("action pipeline requires a store");
  const observers = new Set();
  const activeByKey = new Map();
  let generation = 0;

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
    const previous = key ? activeByKey.get(key) : null;
    if (previous && definition.policy === "join") return previous.task;
    if (previous && definition.policy === "replace") previous.controller.abort();

    const controller = new AbortController();
    const entry = { controller, generation: ++generation, task: null };
    const isCurrent = () => !controller.signal.aborted
      && (definition.policy !== "replace" || !key || activeByKey.get(key) === entry);
    const ensureCurrent = () => {
      if (!isCurrent()) throw cancelledAction(name);
    };

    const task = Promise.resolve().then(async () => {
      emit("start", name, { input, generation: entry.generation });
      try {
        const actionContext = {
          ...context,
          store,
          input,
          signal: controller.signal,
          generation: entry.generation,
          isCurrent,
        };
        const result = await definition.execute(actionContext);
        ensureCurrent();
        let committed;
        if (typeof definition.commit === "function") {
          committed = store.transact(name, state => definition.commit(state, result, input), { source: "action" });
        } else {
          store.touch(name, { source: "action", changed: false });
        }
        ensureCurrent();
        if (typeof definition.effect === "function") {
          await definition.effect({ ...actionContext, result, committed });
          ensureCurrent();
        }
        emit("success", name, { input, result, generation: entry.generation });
        return result;
      } catch (error) {
        const wasCancelled = controller.signal.aborted
          || error?.name === "AbortError"
          || (definition.policy === "replace" && key && activeByKey.get(key) !== entry);
        const reportedError = wasCancelled && error?.name !== "AbortError" ? cancelledAction(name) : error;
        emit(wasCancelled ? "cancelled" : "error", name, {
          input,
          error: reportedError,
          generation: entry.generation,
        });
        if (!wasCancelled && typeof definition.failure === "function") {
          await definition.failure({ ...context, store, input, error: reportedError });
        } else if (!wasCancelled && typeof onError === "function") {
          onError(reportedError, { name, input });
        }
        throw reportedError;
      } finally {
        emit("settled", name, { input, generation: entry.generation });
      }
    });

    entry.task = task;
    if (key) activeByKey.set(key, entry);
    try {
      return await task;
    } finally {
      if (key && activeByKey.get(key) === entry) activeByKey.delete(key);
    }
  }

  return Object.freeze({ run, observe, isRunning: key => activeByKey.has(key) });
}
