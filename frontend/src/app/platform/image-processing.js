"use strict";

// Cancellable, one-worker-per-job image processing boundary.
const workerUrl = COLORLESS_IMAGE_WORKER_URL;
  const activeJobs = new Map();
  let nextJobId = 1;

  function supported() {
    return typeof Worker === "function"
      && typeof createImageBitmap === "function"
      && typeof OffscreenCanvas === "function"
      && typeof OffscreenCanvas.prototype.convertToBlob === "function";
  }

  function cancel(channel) {
    const job = activeJobs.get(channel);
    if (!job) return;
    activeJobs.delete(channel);
    window.clearTimeout(job.timer);
    job.worker.terminate();
    job.reject(new DOMException("Image processing was canceled", "AbortError"));
  }

  function run(channel, file, operation, options = {}, controls = {}) {
    cancel(channel);
    if (!supported()) return Promise.reject(new Error("image-worker-unavailable"));
    const timeoutMs = Math.max(1000, Number(controls.timeoutMs) || 15000);
    const id = nextJobId;
    nextJobId += 1;
    return new Promise((resolve, reject) => {
      const worker = new Worker(workerUrl);
      const timer = window.setTimeout(() => {
        if (activeJobs.get(channel)?.id !== id) return;
        activeJobs.delete(channel);
        worker.terminate();
        reject(new Error("image-processing-timeout"));
      }, timeoutMs);
      const finish = (callback, value) => {
        if (activeJobs.get(channel)?.id !== id) return;
        activeJobs.delete(channel);
        window.clearTimeout(timer);
        worker.terminate();
        callback(value);
      };
      worker.addEventListener("message", (event) => {
        const message = event.data || {};
        if (message.type === "progress") {
          controls.onProgress?.(message.stage);
          return;
        }
        if (message.type === "result") finish(resolve, message.result);
        else if (message.type === "error") finish(reject, new Error(message.message || "image-processing-failed"));
      });
      worker.addEventListener("error", () => finish(reject, new Error("image-worker-failed")), { once: true });
      activeJobs.set(channel, { id, worker, reject, timer });
      worker.postMessage({ id, file, operation, options });
    });
  }

export const ColorlessImageProcessing = Object.freeze({ cancel, run, supported });
