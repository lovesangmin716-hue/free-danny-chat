"use strict";

import { ColorlessImageProcessing } from "./app/platform/image-processing.js";

(async function runImageWorkerBenchmark() {
  const resultNode = document.getElementById("result");
  const longTasks = [];
  const observer = new PerformanceObserver((list) => {
    longTasks.push(...list.getEntries().map((entry) => Math.round(entry.duration * 1000) / 1000));
  });
  observer.observe({ entryTypes: ["longtask"] });
  const startedAt = performance.now();
  try {
    const source = await new Promise((resolve, reject) => {
      const worker = new Worker(COLORLESS_FIXTURE_WORKER_URL);
      worker.addEventListener("message", (event) => {
        worker.terminate();
        resolve(event.data);
      }, { once: true });
      worker.addEventListener("error", () => reject(new Error("fixture-worker-failed")), { once: true });
      worker.postMessage({ width: 4000, height: 3000 });
    });
    const optimized = await ColorlessImageProcessing.run("benchmark", source.blob, "optimize", {
      maxPixels: 32 * 1000 * 1000,
      maxDimension: 16384,
      maxEdge: 2560,
      quality: 0.82,
    }, { timeoutMs: 15000 });
    const canceledJob = ColorlessImageProcessing.run("benchmark-cancel", source.blob, "optimize", {
      maxPixels: 32 * 1000 * 1000,
      maxDimension: 16384,
      maxEdge: 2560,
      quality: 0.82,
    }, { timeoutMs: 15000 });
    const replacementJob = ColorlessImageProcessing.run("benchmark-cancel", source.blob, "optimize", {
      maxPixels: 32 * 1000 * 1000,
      maxDimension: 16384,
      maxEdge: 64,
      quality: 0.7,
    }, { timeoutMs: 15000 });
    const cancellationOutcome = await canceledJob.then(() => "not-canceled", (error) => error.name);
    await replacementJob;
    const bombHeader = new Uint8Array(24);
    bombHeader.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
    new DataView(bombHeader.buffer).setUint32(8, 13, false);
    bombHeader.set([0x49, 0x48, 0x44, 0x52], 12);
    new DataView(bombHeader.buffer).setUint32(16, 50000, false);
    new DataView(bombHeader.buffer).setUint32(20, 50000, false);
    const bombOutcome = await ColorlessImageProcessing.run("benchmark-bomb", new Blob([bombHeader], { type: "image/png" }), "optimize", {
      maxPixels: 32 * 1000 * 1000,
      maxDimension: 16384,
      maxEdge: 2560,
    }).then(() => "accepted", (error) => error.message);
    await new Promise((resolve) => setTimeout(resolve, 100));
    observer.disconnect();
    const report = {
      source: { width: source.width, height: source.height, bytes: source.blob.size },
      output: { width: optimized.width, height: optimized.height, bytes: optimized.blob.size },
      elapsedMs: Math.round((performance.now() - startedAt) * 1000) / 1000,
      longTasks,
      longTasksOver100Ms: longTasks.filter((duration) => duration >= 100).length,
      cancellationOutcome,
      bombOutcome,
      workerSupported: ColorlessImageProcessing.supported(),
    };
    resultNode.textContent = JSON.stringify(report, null, 2);
    document.body.dataset.status = (
      report.longTasksOver100Ms === 0
      && cancellationOutcome === "AbortError"
      && bombOutcome === "image-dimensions-too-large"
    ) ? "passed" : "failed";
  } catch (error) {
    observer.disconnect();
    resultNode.textContent = JSON.stringify({ error: error?.message || String(error) }, null, 2);
    document.body.dataset.status = "failed";
  }
})();
