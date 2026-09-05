import assert from "node:assert/strict";

const values = new Map();
globalThis.window = {
  location: new URL("https://chat.example/app"),
  sessionStorage: {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, String(value)),
  },
};

let source;
globalThis.EventSource = class {
  constructor(url) {
    this.url = url;
    source = this;
  }
  close() {}
};

const { createEventRouter, createRealtimeClient } = await import(
  "../frontend/src/app/platform/events.js"
);

const router = createEventRouter();
let handled = 0;
let release;
router.register("message_created", async () => {
  handled += 1;
  await new Promise((resolve) => { release = resolve; });
});

const event = { event_id: "event-shared", type: "message_created" };
const first = router.dispatch(event);
const concurrent = router.dispatch(event);
release();
assert.deepEqual(await Promise.all([first, concurrent]), [true, true]);
assert.equal(handled, 1, "concurrent copies share one in-flight dispatch");

const client = createRealtimeClient({ url: "/events", router });
client.open();
await source.onmessage({ data: JSON.stringify(event), lastEventId: "7" });
assert.equal(handled, 1, "SSE and /sync share the router event-id cache");
assert.equal(values.get("colorless-realtime-cursor"), "7");

let attempts = 0;
const retryRouter = createEventRouter({ onError: () => {} });
retryRouter.register("message_updated", () => {
  attempts += 1;
  if (attempts === 1) throw new Error("transient handler failure");
});
const retried = { event_id: "event-retry", type: "message_updated" };
await assert.rejects(retryRouter.dispatch(retried));
assert.equal(await retryRouter.dispatch(retried), true);
assert.equal(await retryRouter.dispatch(retried), true);
assert.equal(attempts, 2, "failed events are not marked completed");

console.log("Realtime transport deduplication passed");
