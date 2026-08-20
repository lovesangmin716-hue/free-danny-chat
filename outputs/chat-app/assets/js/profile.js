"use strict";

// Profile palette, avatar editor, image crop, and upload behavior.
function getActiveProfilePalette() {
  if (state.selectedProfilePalette === "default") return DEFAULT_PROFILE_PALETTE;
  if (state.selectedProfilePalette === "custom") return state.customPalette;
  return PROFILE_PALETTES[Number(state.selectedProfilePalette.replace("preset-", ""))] || DEFAULT_PROFILE_PALETTE;
}

function renderProfilePalette() {
  pixelPalette.replaceChildren();
  const colors = getActiveProfilePalette();
  if (!colors.length) {
    const empty = document.createElement("span");
    empty.textContent = "나만의 팔레트를 추가할 수 있어요.";
    pixelPalette.appendChild(empty);
    pixelPalette.appendChild(customColorButton);
    return;
  }
  colors.forEach((color, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `pixel-color${state.selectedProfileColor === color ? " active" : ""}`;
    button.dataset.color = color;
    button.style.backgroundColor = color;
    button.setAttribute("aria-label", `${index + 1}번 색상 선택`);
    button.addEventListener("click", () => {
      state.selectedProfileColor = color;
      customProfileColor.value = color;
      updateProfilePaletteSelection();
    });
    button.addEventListener("dblclick", (event) => {
      event.preventDefault();
      addCustomPaletteColor(color);
    });
    pixelPalette.appendChild(button);
  });
  pixelPalette.appendChild(customColorButton);
}

function updateProfilePaletteSelection() {
  pixelPalette.querySelectorAll(".pixel-color").forEach((button) => {
    button.classList.toggle("active", button.dataset.color === state.selectedProfileColor);
  });
  customPaletteColors.querySelectorAll(".custom-palette-color").forEach((button) => {
    button.classList.toggle("active", button.dataset.color === state.selectedProfileColor);
  });
  announceProfilePixel("색상 선택");
}

function renderProfilePaletteSelect() {
  profilePaletteSelect.replaceChildren();
  const defaultOption = document.createElement("option");
  defaultOption.value = "default";
  defaultOption.textContent = "기본 색 조합";
  defaultOption.selected = state.selectedProfilePalette === "default";
  profilePaletteSelect.appendChild(defaultOption);
  PROFILE_PALETTES.forEach((palette, index) => {
    const option = document.createElement("option");
    option.value = `preset-${index}`;
    option.textContent = PROFILE_PALETTE_NAMES[index];
    option.selected = option.value === state.selectedProfilePalette;
    profilePaletteSelect.appendChild(option);
  });
  const customOption = document.createElement("option");
  customOption.value = "custom";
  customOption.textContent = `나만의 팔레트 (${state.customPalette.length}색)`;
  customOption.selected = state.selectedProfilePalette === "custom";
  profilePaletteSelect.appendChild(customOption);
}

function renderCustomPalette() {
  customPaletteColors.replaceChildren();
  customPaletteCount.textContent = `${state.customPalette.length} / 10`;
  customPaletteEmpty.classList.toggle("hidden", state.customPalette.length > 0);
  state.customPalette.forEach((color) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `custom-palette-color${state.selectedProfileColor === color ? " active" : ""}`;
    button.dataset.color = color;
    button.style.backgroundColor = color;
    button.setAttribute("aria-label", `${color} 색상 선택`);
    button.title = "클릭하여 색상 선택, 두 번 클릭하여 삭제";
    button.addEventListener("click", () => {
      state.selectedProfileColor = color;
      customProfileColor.value = color;
      updateProfilePaletteSelection();
    });
    button.addEventListener("dblclick", (event) => {
      event.preventDefault();
      removeCustomPaletteColor(color);
    });
    customPaletteColors.appendChild(button);
  });
}

function renderPalettePicker() {
  palettePicker.classList.toggle("hidden", !state.palettePickerOpen);
  togglePalettePickerButton.textContent = state.palettePickerOpen ? "닫기" : "색 조합";
}

async function saveCustomPalette() {
  const data = await requestAction("profile.save-palette", "/profile/custom-palette", {
    method: "POST",
    body: JSON.stringify({ colors: state.customPalette }),
  });
  state.messenger.user = data.user;
  if (state.session?.user) state.session.user = data.user;
  state.customPalette = data.user.custom_palette || [];
  renderCustomPalette();
  renderProfilePaletteSelect();
  if (state.selectedProfilePalette === "custom") renderProfilePalette();
}

async function addCustomPaletteColor(color = state.selectedProfileColor) {
  if (state.customPalette.includes(color)) {
    setAppStatus("이미 나만의 팔레트에 있는 색이에요.", "error");
    return;
  }
  if (state.customPalette.length >= 10) {
    setAppStatus("나만의 팔레트에는 색을 10개까지 추가할 수 있어요.", "error");
    return;
  }
  state.customPalette.push(color);
  try {
    await saveCustomPalette();
    setAppStatus("나만의 팔레트에 색을 추가했어요.", "success");
  } catch (error) {
    state.customPalette = state.customPalette.filter((item) => item !== color);
    renderCustomPalette();
    setAppStatus(error.message, "error");
  }
}

async function removeCustomPaletteColor(color) {
  const previousPalette = [...state.customPalette];
  state.customPalette = state.customPalette.filter((item) => item !== color);
  try {
    await saveCustomPalette();
    setAppStatus("나만의 팔레트에서 색을 지웠어요.", "success");
  } catch (error) {
    state.customPalette = previousPalette;
    renderCustomPalette();
    setAppStatus(error.message, "error");
  }
}

function paintProfilePixel(index) {
  if (state.profilePixels[index] === state.selectedProfileColor) return;
  state.profilePixels[index] = state.selectedProfileColor;
  state.profilePixelsDirty = true;
  drawProfilePixel(index);
  renderProfileImagePreview();
}

function eraseProfilePixel(index) {
  if (state.profilePixels[index] === "#ffffff") return;
  state.profilePixels[index] = "#ffffff";
  state.profilePixelsDirty = true;
  drawProfilePixel(index);
  renderProfileImagePreview();
}

function profilePixelCoordinates(index = state.profilePixelCursor) {
  return { row: Math.floor(index / PIXEL_SIDE), column: index % PIXEL_SIDE };
}

function announceProfilePixel(action = "현재 위치") {
  if (!profilePixelStatus) return;
  const { row, column } = profilePixelCoordinates();
  const pixelColor = state.profilePixels[state.profilePixelCursor] || "#ffffff";
  profilePixelStatus.textContent = `${action}. ${row + 1}행 ${column + 1}열, 현재 ${pixelColor}, 선택 색상 ${state.selectedProfileColor}`;
}

function profilePixelCanvasContext() {
  return pixelEditorGrid.getContext("2d", { alpha: false });
}

function drawProfilePixel(index) {
  const context = profilePixelCanvasContext();
  if (!context) return;
  const size = pixelEditorGrid.width / PIXEL_SIDE;
  const { row, column } = profilePixelCoordinates(index);
  context.fillStyle = state.profilePixels[index] || "#ffffff";
  context.fillRect(column * size, row * size, size, size);
  context.strokeStyle = "#e5e5e5";
  context.lineWidth = 1;
  context.strokeRect(column * size + 0.5, row * size + 0.5, size - 1, size - 1);
  if (index === state.profilePixelCursor && !profileSheet.classList.contains("hidden")) drawProfilePixelCursor();
}

function drawProfilePixelCursor() {
  const context = profilePixelCanvasContext();
  if (!context) return;
  const size = pixelEditorGrid.width / PIXEL_SIDE;
  const { row, column } = profilePixelCoordinates();
  context.strokeStyle = "#ffffff";
  context.lineWidth = 4;
  context.strokeRect(column * size + 2, row * size + 2, size - 4, size - 4);
  context.strokeStyle = "#111111";
  context.lineWidth = 2;
  context.strokeRect(column * size + 2, row * size + 2, size - 4, size - 4);
}

function renderProfileEditor() {
  const context = profilePixelCanvasContext();
  if (!context) return;
  context.clearRect(0, 0, pixelEditorGrid.width, pixelEditorGrid.height);
  for (let index = 0; index < PROFILE_PIXEL_COUNT; index += 1) {
    drawProfilePixel(index);
  }
  drawProfilePixelCursor();
  announceProfilePixel();
}

function profilePixelIndexFromPointer(event) {
  const rect = pixelEditorGrid.getBoundingClientRect();
  if (!rect.width || !rect.height) return -1;
  const column = Math.floor(((event.clientX - rect.left) / rect.width) * PIXEL_SIDE);
  const row = Math.floor(((event.clientY - rect.top) / rect.height) * PIXEL_SIDE);
  if (column < 0 || column >= PIXEL_SIDE || row < 0 || row >= PIXEL_SIDE) return -1;
  return row * PIXEL_SIDE + column;
}

function moveProfilePixelCursor(index) {
  const nextIndex = Math.max(0, Math.min(PROFILE_PIXEL_COUNT - 1, index));
  if (nextIndex === state.profilePixelCursor) return;
  const previousIndex = state.profilePixelCursor;
  state.profilePixelPreviousCursor = previousIndex;
  state.profilePixelCursor = nextIndex;
  drawProfilePixel(previousIndex);
  drawProfilePixel(nextIndex);
  announceProfilePixel();
}

function handleProfilePixelPointer(event) {
  const index = profilePixelIndexFromPointer(event);
  if (index < 0) return;
  moveProfilePixelCursor(index);
  paintProfilePixel(index);
  announceProfilePixel("그림");
}

function initializeProfileEditor() {
  if (pixelEditorGrid.dataset.initialized === "true") return;
  pixelEditorGrid.dataset.initialized = "true";
  pixelEditorGrid.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    pixelEditorGrid.focus({ preventScroll: true });
    const index = profilePixelIndexFromPointer(event);
    if (index < 0) return;
    const now = performance.now();
    const isDoubleTap = state.lastPixelTapIndex === index && now - state.lastPixelTapAt < 360;
    state.lastPixelTapIndex = isDoubleTap ? -1 : index;
    state.lastPixelTapAt = isDoubleTap ? 0 : now;
    state.profilePainting = !isDoubleTap;
    moveProfilePixelCursor(index);
    if (isDoubleTap) eraseProfilePixel(index);
    else paintProfilePixel(index);
    announceProfilePixel(isDoubleTap ? "지움" : "그림");
    if (pixelEditorGrid.setPointerCapture) pixelEditorGrid.setPointerCapture(event.pointerId);
  });
  pixelEditorGrid.addEventListener("pointermove", (event) => {
    if (!state.profilePainting) return;
    handleProfilePixelPointer(event);
  });
  const finishPointer = (event) => {
    state.profilePainting = false;
    if (event?.pointerId !== undefined && pixelEditorGrid.hasPointerCapture?.(event.pointerId)) {
      pixelEditorGrid.releasePointerCapture(event.pointerId);
    }
  };
  pixelEditorGrid.addEventListener("pointerup", finishPointer);
  pixelEditorGrid.addEventListener("pointercancel", finishPointer);
  pixelEditorGrid.addEventListener("dblclick", (event) => {
    event.preventDefault();
    const index = profilePixelIndexFromPointer(event);
    if (index < 0) return;
    moveProfilePixelCursor(index);
    eraseProfilePixel(index);
    state.lastPixelTapIndex = -1;
    state.lastPixelTapAt = 0;
    announceProfilePixel("지움");
  });
  pixelEditorGrid.addEventListener("keydown", (event) => {
    const { row, column } = profilePixelCoordinates();
    let nextIndex = state.profilePixelCursor;
    if (event.key === "ArrowLeft") nextIndex = row * PIXEL_SIDE + Math.max(0, column - 1);
    else if (event.key === "ArrowRight") nextIndex = row * PIXEL_SIDE + Math.min(PIXEL_SIDE - 1, column + 1);
    else if (event.key === "ArrowUp") nextIndex = Math.max(0, row - 1) * PIXEL_SIDE + column;
    else if (event.key === "ArrowDown") nextIndex = Math.min(PIXEL_SIDE - 1, row + 1) * PIXEL_SIDE + column;
    else if (event.key === "Home") nextIndex = row * PIXEL_SIDE;
    else if (event.key === "End") nextIndex = row * PIXEL_SIDE + PIXEL_SIDE - 1;
    else if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      paintProfilePixel(state.profilePixelCursor);
      announceProfilePixel("그림");
      return;
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      eraseProfilePixel(state.profilePixelCursor);
      announceProfilePixel("지움");
      return;
    } else return;
    event.preventDefault();
    moveProfilePixelCursor(nextIndex);
  });
}

function buildProfileEditor() {
  initializeProfileEditor();
  renderProfileEditor();
}

function renderProfileImagePreview() {
  const user = state.messenger.user || state.session?.user;
  profilePhotoPreview.replaceChildren(createAvatar(
    getDisplayName(user),
    state.profilePixels,
    null,
    "",
    "",
  ));
  syncProfileImageControls();
}

function syncProfileImageControls() {
  const isBusy = state.profileImagePreparing;
  selectProfilePhotoButton.disabled = false;
  profilePhotoInput.disabled = false;
  saveProfileButton.disabled = isBusy || state.profileCropOpen;
  removeProfilePhotoButton.disabled = isBusy
    || state.profileCropOpen
    || !state.profilePixels.some((color) => color !== "#ffffff");
  profilePhotoZoom.disabled = isBusy;
  cancelProfilePhotoButton.disabled = isBusy;
  saveProfilePhotoButton.disabled = isBusy;
}

function setProfileImagePreparing(isPreparing) {
  state.profileImagePreparing = isPreparing;
  syncProfileImageControls();
}

function decodedImageSize(image) {
  return {
    width: image?.naturalWidth || image?.width || 0,
    height: image?.naturalHeight || image?.height || 0,
  };
}

function profileCropMinimumZoomPercent(width, height) {
  if (!width || !height) return 50;
  const coverScale = Math.max(PROFILE_IMAGE_SIDE / width, PROFILE_IMAGE_SIDE / height);
  const containScale = Math.min(PROFILE_IMAGE_SIDE / width, PROFILE_IMAGE_SIDE / height);
  return Math.max(1, Math.min(50, Math.floor((containScale / coverScale) * 100)));
}

function profileCropGeometry(width, height, zoomPercent) {
  const coverScale = Math.max(PROFILE_IMAGE_SIDE / width, PROFILE_IMAGE_SIDE / height);
  const scale = coverScale * zoomPercent / 100;
  const drawWidth = width * scale;
  const drawHeight = height * scale;
  return {
    drawWidth,
    drawHeight,
    maxOffsetX: Math.abs(drawWidth - PROFILE_IMAGE_SIDE) / 2,
    maxOffsetY: Math.abs(drawHeight - PROFILE_IMAGE_SIDE) / 2,
  };
}

function clampProfileCropOffsets() {
  const { width, height } = decodedImageSize(state.profileCropImage);
  if (!width || !height) return;
  const geometry = profileCropGeometry(width, height, state.profileCropZoomPercent);
  state.profileCropOffsetX = Math.max(-geometry.maxOffsetX, Math.min(geometry.maxOffsetX, state.profileCropOffsetX));
  state.profileCropOffsetY = Math.max(-geometry.maxOffsetY, Math.min(geometry.maxOffsetY, state.profileCropOffsetY));
}

function drawProfileCropPreview() {
  const image = state.profileCropImage;
  if (!image) return;
  const { width, height } = decodedImageSize(image);
  const context = profilePhotoCropCanvas.getContext("2d", { alpha: false });
  if (!context || !width || !height) return;
  const previewScale = profilePhotoCropCanvas.width / PROFILE_IMAGE_SIDE;
  const geometry = profileCropGeometry(width, height, state.profileCropZoomPercent);
  const drawWidth = geometry.drawWidth * previewScale;
  const drawHeight = geometry.drawHeight * previewScale;
  const drawX = profilePhotoCropCanvas.width / 2 + state.profileCropOffsetX * previewScale - drawWidth / 2;
  const drawY = profilePhotoCropCanvas.height / 2 + state.profileCropOffsetY * previewScale - drawHeight / 2;

  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, profilePhotoCropCanvas.width, profilePhotoCropCanvas.height);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(image, drawX, drawY, drawWidth, drawHeight);
  context.strokeStyle = "rgba(0, 0, 0, 0.35)";
  context.lineWidth = 1;
  context.setLineDash([6, 6]);
  for (let index = 1; index < 3; index += 1) {
    const guide = Math.round(profilePhotoCropCanvas.width * index / 3) + 0.5;
    context.beginPath();
    context.moveTo(guide, 0);
    context.lineTo(guide, profilePhotoCropCanvas.height);
    context.moveTo(0, guide);
    context.lineTo(profilePhotoCropCanvas.width, guide);
    context.stroke();
  }
  context.setLineDash([]);
}

function scheduleProfileCropPreview() {
  if (state.profileCropFrame !== null) return;
  state.profileCropFrame = requestAnimationFrame(() => {
    state.profileCropFrame = null;
    drawProfileCropPreview();
  });
}

function resetProfileImageCrop() {
  if (state.profileCropFrame !== null) cancelAnimationFrame(state.profileCropFrame);
  state.profileCropFrame = null;
  state.profileCropImage?.close?.();
  state.profileCropImage = null;
  state.profileCropOpen = false;
  state.profileCropPointer = null;
  state.profileCropZoomPercent = 100;
  state.profileCropOffsetX = 0;
  state.profileCropOffsetY = 0;
  profilePhotoZoom.min = "50";
  profilePhotoZoom.value = "100";
  profilePhotoZoomValue.value = "100%";
  profilePhotoCropCanvas.classList.remove("dragging");
  profilePhotoCrop.classList.add("hidden");
  syncProfileImageControls();
}

async function uploadSelectedProfileImage(file) {
  const contentType = attachmentContentType(file);
  if (!contentType.startsWith("image/") || contentType === "image/gif") {
    profilePhotoInput.value = "";
    setAppStatus("JPG, PNG, WebP, AVIF 또는 HEIC 사진을 선택해 주세요.", "error");
    return;
  }
  if (file.size > PROFILE_IMAGE_SOURCE_BYTES_MAX) {
    profilePhotoInput.value = "";
    setAppStatus("프로필 사진 원본은 50MB 이하만 선택할 수 있어요.", "error");
    return;
  }

  const selectionId = state.profileImageSelectionId + 1;
  state.profileImageSelectionId = selectionId;
  ColorlessImageProcessing.cancel("profile-image");
  resetProfileImageCrop();
  setProfileImagePreparing(true);
  setAppStatus("프로필 사진을 불러오는 중이에요.");
  let decodedImage = null;
  try {
    let cropSource = file;
    if (ColorlessImageProcessing.supported()) {
      const prepared = await ColorlessImageProcessing.run("profile-image", file, "optimize", {
        maxPixels: IMAGE_TOTAL_PIXELS_MAX,
        maxDimension: IMAGE_DIMENSION_MAX,
        maxEdge: 2048,
        quality: 0.92,
      }, {
        timeoutMs: 15000,
        onProgress: (stage) => setAppStatus(imageProgressMessage(stage, "프로필 사진")),
      });
      cropSource = prepared.blob;
    }
    decodedImage = await decodeAttachmentImage(cropSource, 2048, IMAGE_FALLBACK_TOTAL_PIXELS_MAX);
    const { width, height } = decodedImageSize(decodedImage);
    if (!width || !height || width * height > IMAGE_TOTAL_PIXELS_MAX) {
      throw new Error("image-dimensions-too-large");
    }
    if (state.profileImageSelectionId !== selectionId) {
      decodedImage.close?.();
      return;
    }
    state.profileCropImage = decodedImage;
    decodedImage = null;
    state.profileCropOpen = true;
    state.profileCropZoomPercent = 100;
    state.profileCropOffsetX = 0;
    state.profileCropOffsetY = 0;
    profilePhotoZoom.min = String(profileCropMinimumZoomPercent(width, height));
    profilePhotoZoom.value = "100";
    profilePhotoZoomValue.value = "100%";
    profilePhotoCrop.classList.remove("hidden");
    scheduleProfileCropPreview();
    profilePhotoCropCanvas.focus({ preventScroll: true });
    profilePhotoCrop.scrollIntoView({ block: "nearest" });
    setAppStatus("크기와 위치를 맞춘 뒤 32×32 픽셀로 변환해 주세요.");
  } catch (error) {
    if (state.profileImageSelectionId !== selectionId) return;
    decodedImage?.close?.();
    resetProfileImageCrop();
    const message = error?.message === "image-dimensions-too-large"
      ? "이미지 해상도가 너무 커서 처리할 수 없어요."
      : "프로필 사진을 읽지 못했어요. 다른 사진을 선택해 주세요.";
    setAppStatus(message, "error");
  } finally {
    if (state.profileImageSelectionId === selectionId) setProfileImagePreparing(false);
    profilePhotoInput.value = "";
  }
}

function profilePixelColor(red, green, blue) {
  return `#${[red, green, blue].map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function convertCroppedProfileImageToPixels() {
  if (state.profileImagePreparing || !state.profileCropImage) return;
  setProfileImagePreparing(true);
  setAppStatus("사진을 32×32 픽셀로 변환하는 중이에요.");
  try {
    const image = state.profileCropImage;
    const { width, height } = decodedImageSize(image);
    if (!width || !height) throw new Error("image-decode-failed");
    clampProfileCropOffsets();
    const geometry = profileCropGeometry(width, height, state.profileCropZoomPercent);
    const scale = PIXEL_SIDE / PROFILE_IMAGE_SIDE;
    const canvas = createImageCanvas(PIXEL_SIDE, PIXEL_SIDE);
    const context = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
    if (!context) throw new Error("image-canvas-unavailable");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, PIXEL_SIDE, PIXEL_SIDE);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(
      image,
      (PROFILE_IMAGE_SIDE / 2 + state.profileCropOffsetX - geometry.drawWidth / 2) * scale,
      (PROFILE_IMAGE_SIDE / 2 + state.profileCropOffsetY - geometry.drawHeight / 2) * scale,
      geometry.drawWidth * scale,
      geometry.drawHeight * scale,
    );
    const rgba = context.getImageData(0, 0, PIXEL_SIDE, PIXEL_SIDE).data;
    const pixels = [];
    for (let index = 0; index < rgba.length; index += 4) {
      pixels.push(profilePixelColor(rgba[index], rgba[index + 1], rgba[index + 2]));
    }
    state.profilePixels = normalizeProfilePixels(pixels);
    state.profilePixelsDirty = true;
    resetProfileImageCrop();
    buildProfileEditor();
    renderProfileImagePreview();
    pixelEditorGrid.focus({ preventScroll: true });
    pixelEditorGrid.scrollIntoView({ block: "nearest" });
    setAppStatus("32×32 픽셀로 변환했어요. 필요하면 수정한 뒤 프로필을 저장하세요.", "success");
  } catch (error) {
    setAppStatus("사진을 픽셀 프로필로 변환하지 못했어요. 다시 시도해 주세요.", "error");
  } finally {
    setProfileImagePreparing(false);
  }
}

function cancelProfileImageCrop() {
  if (state.profileImagePreparing) return;
  state.profileImageSelectionId += 1;
  ColorlessImageProcessing.cancel("profile-image");
  resetProfileImageCrop();
  selectProfilePhotoButton.focus({ preventScroll: true });
  setAppStatus("프로필 사진 변경을 취소했어요.");
}

function updateProfileCropZoom() {
  const zoomPercent = Number(profilePhotoZoom.value);
  if (!Number.isFinite(zoomPercent)) return;
  state.profileCropZoomPercent = Math.max(
    Number(profilePhotoZoom.min),
    Math.min(Number(profilePhotoZoom.max), zoomPercent),
  );
  profilePhotoZoomValue.value = `${Math.round(state.profileCropZoomPercent)}%`;
  clampProfileCropOffsets();
  scheduleProfileCropPreview();
}

function moveProfileCrop(offsetX, offsetY) {
  state.profileCropOffsetX = offsetX;
  state.profileCropOffsetY = offsetY;
  clampProfileCropOffsets();
  scheduleProfileCropPreview();
}

function finishProfileCropPointer(event) {
  const pointer = state.profileCropPointer;
  if (!pointer || pointer.id !== event.pointerId) return;
  if (profilePhotoCropCanvas.hasPointerCapture(event.pointerId)) {
    profilePhotoCropCanvas.releasePointerCapture(event.pointerId);
  }
  state.profileCropPointer = null;
  profilePhotoCropCanvas.classList.remove("dragging");
}

function clearProfilePixels() {
  if (state.profileImagePreparing || state.profileCropOpen) return;
  state.profilePixels = blankProfilePixels();
  state.profilePixelsDirty = true;
  state.lastPixelTapIndex = -1;
  buildProfileEditor();
  renderProfileImagePreview();
  setAppStatus("픽셀 프로필을 초기화했어요. 저장하기 전까지는 적용되지 않아요.");
}

async function openProfileEditor() {
  const user = state.messenger.user || state.session?.user;
  const loadId = state.profileEditorLoadId + 1;
  state.profileEditorLoadId = loadId;
  resetProfileImageCrop();
  state.profilePixels = blankProfilePixels();
  state.profilePixelsDirty = false;
  state.customPalette = Array.isArray(user?.custom_palette) ? user.custom_palette : [];
  profileDisplayName.value = getDisplayName(user);
  profileFriendCode.value = user?.friend_code || "";
  state.selectedProfileColor = "#000000";
  state.selectedProfilePalette = "default";
  state.palettePickerOpen = false;
  state.lastPixelTapIndex = -1;
  state.lastPixelTapAt = 0;
  state.profilePixelCursor = 0;
  customProfileColor.value = state.selectedProfileColor;
  renderProfilePaletteSelect();
  renderProfilePalette();
  renderCustomPalette();
  renderPalettePicker();
  buildProfileEditor();
  setProfileImagePreparing(state.profileImagePreparing);
  renderProfileImagePreview();
  profileSheet.classList.remove("hidden");
  try {
    const profileArt = await requestAction("profile.load-pixels", "/profile/pixels", {}, {
      key: "profile.pixels",
      policy: "join",
    });
    if (state.profileEditorLoadId !== loadId || state.profilePixelsDirty) return;
    state.profilePixels = normalizeProfilePixels(profileArt.pixels);
    state.profilePixelsDirty = false;
    buildProfileEditor();
    renderProfileImagePreview();
  } catch (error) {
    setAppStatus(error.message, "error");
  }
}

function closeProfileEditor() {
  if (state.profileImagePreparing) setAppStatus("프로필 사진 처리를 취소했어요.");
  state.profileImageSelectionId += 1;
  state.profileEditorLoadId += 1;
  ColorlessImageProcessing.cancel("profile-image");
  setProfileImagePreparing(false);
  resetProfileImageCrop();
  state.profilePainting = false;
  profileSheet.classList.add("hidden");
}

async function saveProfilePixels() {
  if (state.profileImagePreparing) {
    setAppStatus("프로필 사진 처리가 끝날 때까지 잠시 기다려 주세요.");
    return;
  }
  saveProfileButton.disabled = true;
  const user = state.messenger.user || state.session?.user;
  try {
    let data = await requestAction("profile.save", "/profile", {
      method: "POST",
      body: JSON.stringify({
        displayName: profileDisplayName.value.trim(),
        statusMessage: user?.status_message || "",
        friendCode: profileFriendCode.value.trim(),
        pixels: state.profilePixels,
      }),
    });
    if (state.profilePixelsDirty && data.user?.profile_image_url) {
      data = await requestAction("profile.remove-legacy-image", "/profile/image/remove", { method: "POST" });
    }
    state.messenger.user = data.user;
    if (state.session?.user) state.session.user = data.user;
    state.profilePixelsDirty = false;
    closeProfileEditor();
    renderMessenger();
    setAppStatus("프로필을 저장했어요.", "success");
  } catch (error) {
    setAppStatus(error.message, "error");
  } finally {
    saveProfileButton.disabled = false;
  }
}
