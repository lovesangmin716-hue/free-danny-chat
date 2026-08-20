"use strict";

(async function runStaticLoadBenchmark() {
  const result = document.getElementById("result");

  async function measure(label) {
    performance.clearResourceTimings();
    const startedAt = performance.now();
    const indexResponse = await fetch(`/?static-load=${label}-${Date.now()}`);
    const indexText = await indexResponse.text();
    const documentCopy = new DOMParser().parseFromString(indexText, "text/html");
    const assetUrls = [
      ...Array.from(documentCopy.querySelectorAll("script[src]"), (node) => node.getAttribute("src")),
      ...Array.from(documentCopy.querySelectorAll('link[rel="preload"][href]'), (node) => node.getAttribute("href")),
    ].filter(Boolean).map((url) => new URL(url, `${location.origin}/`).href);
    const payloads = await Promise.all(assetUrls.map(async (url) => {
      const response = await fetch(url);
      return { url, bytes: (await response.arrayBuffer()).byteLength };
    }));
    await new Promise((resolve) => setTimeout(resolve, 100));
    const resources = performance.getEntriesByType("resource").map((entry) => ({
      name: entry.name,
      initiatorType: entry.initiatorType,
      transferSize: entry.transferSize,
      encodedBodySize: entry.encodedBodySize,
      duration: Math.round(entry.duration * 1000) / 1000,
    }));
    const fontResources = resources.filter((entry) => entry.name.includes("/fonts/"));
    return {
      duration: Math.round((performance.now() - startedAt) * 1000) / 1000,
      indexBytes: indexText.length,
      decodedAssetBytes: payloads.reduce((total, item) => total + item.bytes, 0),
      requestCount: resources.length,
      resourceTransferBytes: resources.reduce((total, entry) => total + entry.transferSize, 0),
      fontRequestCount: fontResources.length,
      fontTransferBytes: fontResources.reduce((total, entry) => total + entry.transferSize, 0),
      cacheHitResources: resources.filter((entry) => entry.transferSize === 0).length,
      resources,
    };
  }

  try {
    const cold = await measure("cold");
    const warm = await measure("warm");
    const paints = Object.fromEntries(performance.getEntriesByType("paint").map((entry) => [entry.name, Math.round(entry.startTime * 1000) / 1000]));
    const report = { viewport: "390x844", paints, cold, warm };
    result.textContent = JSON.stringify(report, null, 2);
    document.body.dataset.status = (
      cold.fontRequestCount === 1
      && cold.fontTransferBytes <= 250 * 1024
      && warm.cacheHitResources > 0
    ) ? "passed" : "failed";
  } catch (error) {
    result.textContent = JSON.stringify({ error: error?.message || String(error) }, null, 2);
    document.body.dataset.status = "failed";
  }
})();
