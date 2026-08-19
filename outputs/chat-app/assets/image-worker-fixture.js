"use strict";

self.addEventListener("message", async (event) => {
  const width = Math.max(1, Number(event.data?.width) || 4000);
  const height = Math.max(1, Number(event.data?.height) || 3000);
  const canvas = new OffscreenCanvas(width, height);
  const context = canvas.getContext("2d", { alpha: false });
  for (let row = 0; row < 24; row += 1) {
    context.fillStyle = `hsl(${row * 15} 70% 55%)`;
    context.fillRect(0, Math.floor(height * row / 24), width, Math.ceil(height / 24));
  }
  const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: 0.9 });
  postMessage({ blob, width, height });
});
