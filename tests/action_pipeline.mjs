import assert from "node:assert/strict";

import { createActionPipeline } from "../frontend/src/app/platform/pipeline.js";
import { createStore } from "../frontend/src/app/platform/store.js";

const store = createStore({ committed: [] });
const failures = [];
const phases = [];
const pipeline = createActionPipeline({ store, onError: (error) => failures.push(error) });
pipeline.observe((event) => phases.push(`${event.phase}:${event.name}`));

let releaseOld;
let oldSignal;
const oldTask = pipeline.run("search.old", null, {
  key: "search",
  policy: "replace",
  execute: ({ signal }) => {
    oldSignal = signal;
    return new Promise((resolve) => { releaseOld = resolve; });
  },
  commit: (state, result) => state.committed.push(result),
});
const oldRejection = assert.rejects(oldTask, { name: "AbortError" });
await Promise.resolve();

const latest = pipeline.run("search.latest", null, {
  key: "search",
  policy: "replace",
  execute: ({ signal, isCurrent }) => {
    assert.equal(signal.aborted, false);
    assert.equal(isCurrent(), true);
    return "latest";
  },
  commit: (state, result) => state.committed.push(result),
});
assert.equal(oldSignal.aborted, true, "replace aborts the previous generation");
assert.equal(await latest, "latest");
releaseOld("stale");
await oldRejection;
assert.deepEqual(store.state.committed, ["latest"], "a replaced result cannot commit");
assert.deepEqual(failures, [], "expected cancellation does not invoke global failure UI");
assert.ok(phases.includes("cancelled:search.old"));

let releaseJoined;
let joinExecutions = 0;
const joinDefinition = {
  key: "bootstrap",
  policy: "join",
  execute: () => {
    joinExecutions += 1;
    return new Promise((resolve) => { releaseJoined = resolve; });
  },
};
const joinedA = pipeline.run("bootstrap", null, joinDefinition);
await Promise.resolve();
const joinedB = pipeline.run("bootstrap", null, joinDefinition);
releaseJoined("shared");
assert.deepEqual(await Promise.all([joinedA, joinedB]), ["shared", "shared"]);
assert.equal(joinExecutions, 1, "join keeps the existing request contract");

console.log("Action pipeline replacement guards passed");
