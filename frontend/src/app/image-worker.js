"use strict";

const IMAGE_HEADER_BYTES = 512 * 1024;

function uint24(view, offset) {
  return view.getUint8(offset) | (view.getUint8(offset + 1) << 8) | (view.getUint8(offset + 2) << 16);
}

function jpegOrientation(view, start, length) {
  if (length < 14 || String.fromCharCode(...new Uint8Array(view.buffer, view.byteOffset + start, 6)) !== "Exif\0\0") return 1;
  const tiff = start + 6;
  const little = view.getUint16(tiff, false) === 0x4949;
  const read16 = (offset) => view.getUint16(offset, little);
  const read32 = (offset) => view.getUint32(offset, little);
  if (read16(tiff + 2) !== 42) return 1;
  const ifd = tiff + read32(tiff + 4);
  if (ifd + 2 > start + length) return 1;
  const count = read16(ifd);
  for (let index = 0; index < count; index += 1) {
    const entry = ifd + 2 + index * 12;
    if (entry + 12 > start + length) break;
    if (read16(entry) === 0x0112) return read16(entry + 8) || 1;
  }
  return 1;
}

function parseJpeg(view) {
  if (view.byteLength < 4 || view.getUint16(0, false) !== 0xffd8) return null;
  let offset = 2;
  let orientation = 1;
  const sofMarkers = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
  while (offset + 4 <= view.byteLength) {
    if (view.getUint8(offset) !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = view.getUint8(offset + 1);
    offset += 2;
    if (marker === 0xd8 || marker === 0xd9 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset + 2 > view.byteLength) break;
    const segmentLength = view.getUint16(offset, false);
    if (segmentLength < 2 || offset + segmentLength > view.byteLength) break;
    if (marker === 0xe1) orientation = jpegOrientation(view, offset + 2, segmentLength - 2);
    if (sofMarkers.has(marker) && segmentLength >= 7) {
      let width = view.getUint16(offset + 5, false);
      let height = view.getUint16(offset + 3, false);
      if (orientation >= 5 && orientation <= 8) [width, height] = [height, width];
      return { width, height, orientation, format: "jpeg" };
    }
    offset += segmentLength;
  }
  return null;
}

function parsePng(view) {
  if (view.byteLength < 24 || view.getUint32(0, false) !== 0x89504e47 || view.getUint32(4, false) !== 0x0d0a1a0a) return null;
  return { width: view.getUint32(16, false), height: view.getUint32(20, false), orientation: 1, format: "png" };
}

function parseWebp(view) {
  if (view.byteLength < 30 || view.getUint32(0, false) !== 0x52494646 || view.getUint32(8, false) !== 0x57454250) return null;
  const chunk = view.getUint32(12, false);
  if (chunk === 0x56503858) {
    return { width: uint24(view, 24) + 1, height: uint24(view, 27) + 1, orientation: 1, format: "webp" };
  }
  if (chunk === 0x5650384c && view.byteLength >= 25) {
    const bits = view.getUint32(21, true);
    return { width: (bits & 0x3fff) + 1, height: ((bits >> 14) & 0x3fff) + 1, orientation: 1, format: "webp" };
  }
  if (chunk === 0x56503820 && view.byteLength >= 30) {
    return { width: view.getUint16(26, true) & 0x3fff, height: view.getUint16(28, true) & 0x3fff, orientation: 1, format: "webp" };
  }
  return null;
}

async function readImageMetadata(file) {
  const header = await file.slice(0, IMAGE_HEADER_BYTES).arrayBuffer();
  const view = new DataView(header);
  return parseJpeg(view) || parsePng(view) || parseWebp(view);
}

function validateDimensions(width, height, options) {
  const maxPixels = Number(options.maxPixels) || 0;
  const maxDimension = Number(options.maxDimension) || 0;
  if (!Number.isFinite(width) || !Number.isFinite(height) || width < 1 || height < 1) throw new Error("image-decode-failed");
  if ((maxPixels && width * height > maxPixels) || (maxDimension && Math.max(width, height) > maxDimension)) {
    throw new Error("image-dimensions-too-large");
  }
}

function targetSize(width, height, maxEdge) {
  const scale = maxEdge ? Math.min(1, maxEdge / Math.max(width, height)) : 1;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

async function decodeBitmap(file, options) {
  postMessage({ type: "progress", stage: "metadata" });
  const metadata = await readImageMetadata(file);
  if (metadata) validateDimensions(metadata.width, metadata.height, options);
  const intended = metadata ? targetSize(metadata.width, metadata.height, Number(options.maxEdge) || 0) : null;
  const bitmapOptions = { imageOrientation: "from-image" };
  if (intended && (intended.width !== metadata.width || intended.height !== metadata.height)) {
    bitmapOptions.resizeWidth = intended.width;
    bitmapOptions.resizeHeight = intended.height;
    bitmapOptions.resizeQuality = "high";
  }
  postMessage({ type: "progress", stage: "decode" });
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, bitmapOptions);
  } catch {
    bitmap = await createImageBitmap(file);
  }
  validateDimensions(bitmap.width, bitmap.height, options);
  return { bitmap, metadata: metadata || { width: bitmap.width, height: bitmap.height, orientation: 1, format: "decoded" } };
}

function canvas2d(width, height, alpha) {
  const canvas = new OffscreenCanvas(width, height);
  const context = canvas.getContext("2d", { alpha });
  if (!context) throw new Error("image-canvas-unavailable");
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  return { canvas, context };
}

async function encodeWithinBudget(canvas, qualities, maxBytes) {
  let lastBlob = null;
  for (const quality of qualities) {
    lastBlob = await canvas.convertToBlob({ type: "image/webp", quality });
    if (!maxBytes || (lastBlob.size > 0 && lastBlob.size <= maxBytes)) return lastBlob;
    lastBlob = null;
  }
  if (!lastBlob) throw new Error("image-output-too-large");
  return lastBlob;
}

async function optimize(file, options) {
  const { bitmap, metadata } = await decodeBitmap(file, options);
  try {
    const { canvas, context } = canvas2d(bitmap.width, bitmap.height, true);
    postMessage({ type: "progress", stage: "resize" });
    context.drawImage(bitmap, 0, 0, bitmap.width, bitmap.height);
    postMessage({ type: "progress", stage: "encode" });
    const blob = await canvas.convertToBlob({ type: "image/webp", quality: Number(options.quality) || 0.82 });
    return { blob, width: bitmap.width, height: bitmap.height, sourceWidth: metadata.width, sourceHeight: metadata.height };
  } finally {
    bitmap.close();
  }
}

async function squareBundle(file, options) {
  const decodeOptions = { ...options, maxEdge: Number(options.decodeEdge) || 4096 };
  const { bitmap, metadata } = await decodeBitmap(file, decodeOptions);
  try {
    const side = Number(options.side) || 1024;
    const thumbSide = Number(options.thumbSide) || 128;
    const { canvas, context } = canvas2d(side, side, false);
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, side, side);
    const scale = Math.max(side / bitmap.width, side / bitmap.height);
    const drawWidth = bitmap.width * scale;
    const drawHeight = bitmap.height * scale;
    postMessage({ type: "progress", stage: "resize" });
    context.drawImage(bitmap, (side - drawWidth) / 2, (side - drawHeight) / 2, drawWidth, drawHeight);
    postMessage({ type: "progress", stage: "encode" });
    const imageBlob = await encodeWithinBudget(canvas, options.qualities || [0.86, 0.74, 0.62], Number(options.maxBytes) || 0);
    const thumb = canvas2d(thumbSide, thumbSide, false);
    thumb.context.drawImage(canvas, 0, 0, thumbSide, thumbSide);
    const thumbnailBlob = await thumb.canvas.convertToBlob({ type: "image/webp", quality: 0.8 });
    return { imageBlob, thumbnailBlob, sourceWidth: metadata.width, sourceHeight: metadata.height };
  } finally {
    bitmap.close();
  }
}

async function cropBundle(file, options) {
  const { bitmap } = await decodeBitmap(file, options);
  try {
    const side = Number(options.side) || 1024;
    const thumbSide = Number(options.thumbSide) || 128;
    const { canvas, context } = canvas2d(side, side, false);
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, side, side);
    postMessage({ type: "progress", stage: "resize" });
    context.drawImage(bitmap, Number(options.x), Number(options.y), Number(options.drawWidth), Number(options.drawHeight));
    postMessage({ type: "progress", stage: "encode" });
    const imageBlob = await encodeWithinBudget(canvas, options.qualities || [0.86, 0.74, 0.62], Number(options.maxBytes) || 0);
    const thumb = canvas2d(thumbSide, thumbSide, false);
    thumb.context.drawImage(canvas, 0, 0, thumbSide, thumbSide);
    const thumbnailBlob = await thumb.canvas.convertToBlob({ type: "image/webp", quality: 0.8 });
    return { imageBlob, thumbnailBlob };
  } finally {
    bitmap.close();
  }
}

self.addEventListener("message", async (event) => {
  const { id, file, operation, options = {} } = event.data || {};
  try {
    if (!(file instanceof Blob)) throw new Error("image-file-required");
    let result;
    if (operation === "optimize") result = await optimize(file, options);
    else if (operation === "square") result = await squareBundle(file, options);
    else if (operation === "crop") result = await cropBundle(file, options);
    else throw new Error("image-operation-unsupported");
    postMessage({ type: "result", id, result });
  } catch (error) {
    postMessage({ type: "error", id, message: error?.message || "image-processing-failed" });
  }
});
