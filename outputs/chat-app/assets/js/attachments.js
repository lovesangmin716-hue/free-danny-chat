"use strict";

// Chat attachment selection, client-side image optimization, and pre-upload.
function attachmentKindAt(x, y) {
  const distance = Math.hypot(x, y);
  if (distance < 28 || x < 0 || y > 0) return "";
  const angle = Math.atan2(-y, x);
  if (angle < 0 || angle > Math.PI / 2) return "";
  const segment = Math.min(3, Math.floor(angle / (Math.PI / 8)));
  return ["camera", "file", "pdf", "photo"][segment];
}

function showAttachmentGuide() {
  chatAttachmentGuide.classList.remove("hidden");
}

function highlightAttachmentGuide(kind) {
  chatAttachmentGuideItems.forEach((item) => item.classList.toggle("active", item.dataset.kind === kind));
}

function updateAttachmentSwipe(event) {
  const swipe = state.chatAttachmentDrag;
  swipe.kind = attachmentKindAt(event.clientX - swipe.startX, event.clientY - swipe.startY);
  chatAttachmentButton.classList.toggle("swiping", Boolean(swipe.kind));
  highlightAttachmentGuide(swipe.kind);
}

function resetAttachmentSwipe() {
  if (state.chatAttachmentGuideTimer) {
    clearTimeout(state.chatAttachmentGuideTimer);
    state.chatAttachmentGuideTimer = null;
  }
  state.chatAttachmentDrag = { active: false, kind: "", startX: 0, startY: 0 };
  chatAttachmentButton.classList.remove("swiping");
  chatAttachmentGuide.classList.add("hidden");
  highlightAttachmentGuide("");
}

function openAttachmentPicker(kind) {
  if (kind !== "photo" && kind !== "pdf") {
    setAppStatus("준비 중인 기능이에요.");
    return;
  }
  chatAttachmentInput.accept = kind === "pdf"
    ? "application/pdf"
    : "image/*";
  chatAttachmentInput.value = "";
  try {
    if (typeof chatAttachmentInput.showPicker === "function") {
      chatAttachmentInput.showPicker();
      return;
    }
  } catch {}
  chatAttachmentInput.click();
}

function renderChatAttachmentTray() {
  chatAttachmentTray.classList.add("hidden");
}

function renderChatAttachmentPreview() {
  const file = state.chatAttachment;
  chatAttachmentPreview.classList.toggle("hidden", !file);
  chatAttachmentName.textContent = file ? `${state.chatAttachmentType === "application/pdf" ? "PDF" : "Photo"}: ${file.name}` : "";
}

function discardUploadedAttachment(attachment) {
  if (!attachment?.url || !state.session?.user) return Promise.resolve();
  return api("/uploads/discard", {
    method: "POST",
    body: JSON.stringify({ url: attachment.url }),
  }).catch(() => {});
}

function discardChatAttachmentUpload(job) {
  if (!job || job.discardRequested) return;
  job.discardRequested = true;
  void job.promise.then(({ attachment }) => {
    if (!attachment || job.discarded) return;
    job.discarded = true;
    return discardUploadedAttachment(attachment);
  });
}

function clearChatAttachment(options = {}) {
  const preserveUpload = options?.preserveUpload === true;
  if (!preserveUpload) discardChatAttachmentUpload(state.chatAttachmentUpload);
  ColorlessImageProcessing.cancel("chat-attachment");
  state.chatAttachmentSelectionId += 1;
  state.chatAttachment = null;
  state.chatAttachmentType = "";
  state.chatAttachmentPreparing = false;
  state.chatAttachmentUpload = null;
  chatAttachmentInput.value = "";
  renderChatAttachmentPreview();
}

function attachmentContentType(file) {
  const declaredType = (file.type || "").toLowerCase();
  const supportedTypes = ["image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heif", "image/avif", "application/pdf"];
  if (supportedTypes.includes(declaredType)) return declaredType;
  const extension = (file.name || "").split(".").pop()?.toLowerCase();
  return {
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    png: "image/png",
    gif: "image/gif",
    webp: "image/webp",
    heic: "image/heic",
    heif: "image/heif",
    avif: "image/avif",
    pdf: "application/pdf",
  }[extension] || "";
}

function formatAttachmentSize(byteCount) {
  if (byteCount < 1024 * 1024) return `${Math.max(1, Math.round(byteCount / 1024))}KB`;
  return `${(byteCount / (1024 * 1024)).toFixed(1)}MB`;
}

function optimizedImageName(filename) {
  const basename = (filename || "image").replace(/\.[^.]+$/, "").trim().slice(0, 100) || "image";
  return `${basename}.webp`;
}

function decodeImageElement(file) {
  return new Promise((resolve, reject) => {
    const imageUrl = URL.createObjectURL(file);
    const image = new Image();
    image.addEventListener("load", () => {
      URL.revokeObjectURL(imageUrl);
      resolve(image);
    }, { once: true });
    image.addEventListener("error", () => {
      URL.revokeObjectURL(imageUrl);
      reject(new Error("image-decode-failed"));
    }, { once: true });
    image.src = imageUrl;
  });
}

async function decodeAttachmentImage(file, maxEdge = 0, maxPixels = IMAGE_FALLBACK_TOTAL_PIXELS_MAX) {
  let image;
  if (typeof createImageBitmap === "function") {
    try {
      image = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      try {
        image = await createImageBitmap(file);
      } catch {}
    }
  }
  if (!image) image = await decodeImageElement(file);
  const { width, height } = decodedImageSize(image);
  if (!width || !height || width * height > maxPixels || Math.max(width, height) > IMAGE_DIMENSION_MAX) {
    image.close?.();
    throw new Error("image-dimensions-too-large");
  }
  if (!maxEdge || Math.max(width, height) <= maxEdge || typeof createImageBitmap !== "function") {
    return image;
  }
  const scale = maxEdge / Math.max(width, height);
  try {
    const resized = await createImageBitmap(image, {
      resizeWidth: Math.max(1, Math.round(width * scale)),
      resizeHeight: Math.max(1, Math.round(height * scale)),
      resizeQuality: "high",
    });
    image.close?.();
    return resized;
  } catch {
    return image;
  }
}

function createImageCanvas(width, height) {
  const canUseOffscreenCanvas = typeof OffscreenCanvas === "function"
    && typeof OffscreenCanvas.prototype.convertToBlob === "function";
  return canUseOffscreenCanvas
    ? new OffscreenCanvas(width, height)
    : Object.assign(document.createElement("canvas"), { width, height });
}

function canvasToWebpBlob(canvas, quality = IMAGE_WEBP_QUALITY) {
  if (typeof canvas.convertToBlob === "function") {
    return canvas.convertToBlob({ type: "image/webp", quality });
  }
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => blob ? resolve(blob) : reject(new Error("image-encode-failed")),
      "image/webp",
      quality,
    );
  });
}

async function optimizeAttachmentImageOnMain(file) {
  const bitmap = await decodeAttachmentImage(file, IMAGE_EDGE_PIXELS_MAX, IMAGE_FALLBACK_TOTAL_PIXELS_MAX);
  try {
    const width = bitmap.width;
    const height = bitmap.height;
    if (!width || !height || width * height > IMAGE_TOTAL_PIXELS_MAX) {
      throw new Error("image-dimensions-too-large");
    }

    const scale = Math.min(1, IMAGE_EDGE_PIXELS_MAX / Math.max(width, height));
    const targetWidth = Math.max(1, Math.round(width * scale));
    const targetHeight = Math.max(1, Math.round(height * scale));
    const canvas = createImageCanvas(targetWidth, targetHeight);
    const context = canvas.getContext("2d", { alpha: true });
    if (!context) throw new Error("image-canvas-unavailable");
    context.drawImage(bitmap, 0, 0, targetWidth, targetHeight);

    const blob = await canvasToWebpBlob(canvas);
    if (blob.type !== "image/webp" || !blob.size || blob.size > file.size * IMAGE_REQUIRED_SAVINGS_RATIO) {
      return file;
    }
    return new File([blob], optimizedImageName(file.name), {
      type: "image/webp",
      lastModified: file.lastModified,
    });
  } finally {
    bitmap.close?.();
  }
}

function imageProgressMessage(stage, subject = "이미지") {
  return {
    metadata: `${subject} 해상도를 확인하는 중이에요.`,
    decode: `${subject}를 안전한 크기로 읽는 중이에요.`,
    resize: `${subject} 크기를 줄이는 중이에요.`,
    encode: `${subject}를 WebP로 저장하는 중이에요.`,
  }[stage] || `${subject}를 처리하는 중이에요.`;
}

async function optimizeAttachmentImage(file) {
  if (!ColorlessImageProcessing.supported()) return optimizeAttachmentImageOnMain(file);
  const result = await ColorlessImageProcessing.run("chat-attachment", file, "optimize", {
    maxPixels: IMAGE_TOTAL_PIXELS_MAX,
    maxDimension: IMAGE_DIMENSION_MAX,
    maxEdge: IMAGE_EDGE_PIXELS_MAX,
    quality: IMAGE_WEBP_QUALITY,
  }, {
    timeoutMs: 15000,
    onProgress: (stage) => setAppStatus(imageProgressMessage(stage)),
  });
  const blob = result.blob;
  if (blob.type !== "image/webp" || !blob.size || blob.size > file.size * IMAGE_REQUIRED_SAVINGS_RATIO) return file;
  return new File([blob], optimizedImageName(file.name), {
    type: "image/webp",
    lastModified: file.lastModified,
  });
}

async function prepareChatAttachment(file, contentType) {
  const canOptimize = contentType.startsWith("image/")
    && contentType !== "image/gif"
    && file.size >= IMAGE_OPTIMIZE_BYTES_MIN;
  if (!canOptimize) return file;
  return optimizeAttachmentImage(file);
}

async function selectChatAttachment(file) {
  clearChatAttachment();
  const selectionId = state.chatAttachmentSelectionId + 1;
  state.chatAttachmentSelectionId = selectionId;

  const contentType = attachmentContentType(file);
  if (!contentType) {
    clearChatAttachment();
    setAppStatus("사진 또는 PDF 파일만 보낼 수 있어요.", "error");
    return false;
  }
  const sourceLimit = contentType.startsWith("image/") && contentType !== "image/gif"
    ? IMAGE_SOURCE_BYTES_MAX
    : ATTACHMENT_UPLOAD_BYTES_MAX;
  if (file.size > sourceLimit) {
    clearChatAttachment();
    setAppStatus(
      sourceLimit === IMAGE_SOURCE_BYTES_MAX
        ? "원본 이미지는 50MB 이하만 선택할 수 있어요."
        : "PDF와 GIF는 8MB 이하만 보낼 수 있어요.",
      "error",
    );
    return false;
  }

  const shouldShowProgress = contentType.startsWith("image/")
    && contentType !== "image/gif"
    && file.size >= IMAGE_OPTIMIZE_BYTES_MIN;
  if (shouldShowProgress) {
    state.chatAttachmentPreparing = true;
    setAppStatus("이미지 크기를 줄이는 중이에요.");
  }

  let preparedFile = file;
  try {
    preparedFile = await prepareChatAttachment(file, contentType);
  } catch (error) {
    if (state.chatAttachmentSelectionId !== selectionId) return false;
    if (error?.message === "image-dimensions-too-large") {
      clearChatAttachment();
      setAppStatus("이미지 해상도가 너무 커서 처리할 수 없어요.", "error");
      return false;
    }
    if (file.size > ATTACHMENT_UPLOAD_BYTES_MAX) {
      clearChatAttachment();
      setAppStatus("이 브라우저에서 이미지를 8MB 이하로 줄이지 못했어요.", "error");
      return false;
    }
    setAppStatus("이미지 최적화를 건너뛰고 원본을 첨부했어요.");
  }

  if (state.chatAttachmentSelectionId !== selectionId) return false;
  if (preparedFile.size > ATTACHMENT_UPLOAD_BYTES_MAX) {
    clearChatAttachment();
    setAppStatus("압축 후에도 이미지가 8MB를 넘어 전송할 수 없어요.", "error");
    return false;
  }

  state.chatAttachment = preparedFile;
  state.chatAttachmentType = attachmentContentType(preparedFile);
  state.chatAttachmentUpload = startChatAttachmentUpload(
    selectionId,
    preparedFile,
    state.chatAttachmentType,
  );
  state.chatAttachmentPreparing = false;
  state.chatAttachmentTrayOpen = false;
  resetAttachmentSwipe();
  renderChatAttachmentTray();
  renderChatAttachmentPreview();
  if (preparedFile !== file) {
    setAppStatus(
      `이미지를 ${formatAttachmentSize(file.size)}에서 ${formatAttachmentSize(preparedFile.size)}로 줄였어요.`,
      "success",
    );
  }
  return true;
}

function pastedChatFile(clipboardData) {
  if (!clipboardData) return null;
  const directFile = Array.from(clipboardData.files || [])[0];
  if (directFile) return directFile;
  for (const item of Array.from(clipboardData.items || [])) {
    if (item.kind !== "file") continue;
    const file = item.getAsFile();
    if (file) return file;
  }
  return null;
}

async function uploadChatAttachment(file, contentType) {
  const grantResult = await requestAction("attachments.grant", "/uploads/grant", {
    method: "POST",
    body: JSON.stringify({ name: file.name, type: contentType, size: file.size }),
  });
  const upload = grantResult.upload;
  try {
    await requestAction("attachments.transfer", upload.url, {
      method: upload.method,
      headers: upload.headers,
      body: file,
    });
    const completed = await requestAction("attachments.complete", "/uploads/complete", {
      method: "POST",
      body: JSON.stringify({ id: upload.id }),
    });
    return completed.attachment;
  } catch (error) {
    void api("/uploads/discard", {
      method: "POST",
      body: JSON.stringify({ url: `/uploads/${upload.id}` }),
    }).catch(() => {});
    throw error;
  }
}

function startChatAttachmentUpload(selectionId, file, contentType) {
  const job = {
    selectionId,
    discardRequested: false,
    discarded: false,
    promise: null,
  };
  job.promise = uploadChatAttachment(file, contentType)
    .then((attachment) => ({ attachment, error: null }))
    .catch((error) => ({ attachment: null, error }));
  return job;
}
