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
  const data = await api("/profile/custom-palette", {
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
  const cell = state.profileCells[index];
  if (cell) cell.style.backgroundColor = state.selectedProfileColor;
}

function eraseProfilePixel(index) {
  if (state.profilePixels[index] === "#ffffff") return;
  state.profilePixels[index] = "#ffffff";
  const cell = state.profileCells[index];
  if (cell) cell.style.backgroundColor = "#ffffff";
}

function buildProfileEditor() {
  pixelEditorGrid.replaceChildren();
  state.profileCells = [];
  for (let index = 0; index < PROFILE_PIXEL_COUNT; index += 1) {
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "pixel-cell";
    cell.setAttribute("aria-label", `${Math.floor(index / PIXEL_SIDE) + 1}행 ${(index % PIXEL_SIDE) + 1}열 픽셀`);
    cell.style.backgroundColor = state.profilePixels[index];
    cell.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const isDoubleTap = state.lastPixelTapIndex === index;
      state.lastPixelTapIndex = isDoubleTap ? -1 : index;
      state.profilePainting = true;
      if (isDoubleTap) {
        eraseProfilePixel(index);
      } else {
        paintProfilePixel(index);
      }
    });
    cell.addEventListener("dblclick", (event) => {
      event.preventDefault();
      state.lastPixelTapIndex = -1;
      eraseProfilePixel(index);
    });
    cell.addEventListener("pointerenter", () => {
      if (state.profilePainting) paintProfilePixel(index);
    });
    state.profileCells.push(cell);
    pixelEditorGrid.appendChild(cell);
  }
}

function revokeProfileImagePreview() {
  if (!state.profileImagePreviewUrl) return;
  URL.revokeObjectURL(state.profileImagePreviewUrl);
  state.profileImagePreviewUrl = "";
}

function renderProfileImagePreview() {
  const user = state.messenger.user || state.session?.user;
  const imageUrl = state.profileImagePreviewUrl || state.profileImageUrl;
  profilePhotoPreview.replaceChildren(createAvatar(
    getDisplayName(user),
    state.profilePixels,
    null,
    "",
    imageUrl,
  ));
  syncProfileImageControls();
}

function syncProfileImageControls() {
  const isBusy = state.profileImagePreparing;
  selectProfilePhotoButton.disabled = isBusy;
  profilePhotoInput.disabled = isBusy;
  saveProfileButton.disabled = isBusy || state.profileCropOpen;
  removeProfilePhotoButton.disabled = isBusy || state.profileCropOpen || !state.profileImageUrl;
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

async function createProfileImageFile() {
  const image = state.profileCropImage;
  const { width, height } = decodedImageSize(image);
  if (!image || !width || !height) throw new Error("image-decode-failed");
  clampProfileCropOffsets();
  const geometry = profileCropGeometry(width, height, state.profileCropZoomPercent);
  const canvas = createImageCanvas(PROFILE_IMAGE_SIDE, PROFILE_IMAGE_SIDE);
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("image-canvas-unavailable");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, PROFILE_IMAGE_SIDE, PROFILE_IMAGE_SIDE);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(
    image,
    PROFILE_IMAGE_SIDE / 2 + state.profileCropOffsetX - geometry.drawWidth / 2,
    PROFILE_IMAGE_SIDE / 2 + state.profileCropOffsetY - geometry.drawHeight / 2,
    geometry.drawWidth,
    geometry.drawHeight,
  );

  let imageFile = null;
  for (const quality of PROFILE_IMAGE_WEBP_QUALITIES) {
    const blob = await canvasToWebpBlob(canvas, quality);
    if (blob.type === "image/webp" && blob.size > 0 && blob.size <= PROFILE_IMAGE_UPLOAD_BYTES_MAX) {
      imageFile = new File([blob], "profile.webp", { type: "image/webp", lastModified: Date.now() });
      break;
    }
  }
  if (!imageFile) throw new Error("profile-image-too-large");

  const thumbnailCanvas = createImageCanvas(PROFILE_THUMBNAIL_SIDE, PROFILE_THUMBNAIL_SIDE);
  const thumbnailContext = thumbnailCanvas.getContext("2d", { alpha: false });
  if (!thumbnailContext) throw new Error("image-canvas-unavailable");
  thumbnailContext.imageSmoothingEnabled = true;
  thumbnailContext.imageSmoothingQuality = "high";
  thumbnailContext.drawImage(canvas, 0, 0, PROFILE_THUMBNAIL_SIDE, PROFILE_THUMBNAIL_SIDE);
  const thumbnailBlob = await canvasToWebpBlob(thumbnailCanvas, 0.8);
  const thumbnailFile = new File([thumbnailBlob], "profile-thumb.webp", {
    type: "image/webp",
    lastModified: Date.now(),
  });
  return { imageFile, thumbnailFile };
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
  resetProfileImageCrop();
  setProfileImagePreparing(true);
  setAppStatus("프로필 사진을 불러오는 중이에요.");
  let decodedImage = null;
  try {
    decodedImage = await decodeAttachmentImage(file, 4096);
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
    setAppStatus("크기와 위치를 맞춘 뒤 저장해 주세요.");
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

async function saveCroppedProfileImage() {
  if (state.profileImagePreparing || !state.profileCropImage) return;
  const selectionId = state.profileImageSelectionId;
  setProfileImagePreparing(true);
  setAppStatus("프로필 사진을 1024×1024로 저장하는 중이에요.");
  try {
    const { imageFile, thumbnailFile } = await createProfileImageFile();
    if (state.profileImageSelectionId !== selectionId) return;
    const sizeHeader = new ArrayBuffer(4);
    new DataView(sizeHeader).setUint32(0, imageFile.size, false);
    const profileBundle = new Blob([sizeHeader, imageFile, thumbnailFile], {
      type: "application/x-colorless-profile-bundle",
    });
    const data = await api("/profile/image", {
      method: "POST",
      headers: { "Content-Type": profileBundle.type },
      body: profileBundle,
    });
    if (state.profileImageSelectionId !== selectionId) return;
    state.messenger.user = data.user;
    if (state.session?.user) state.session.user = data.user;
    state.profileImageUrl = data.user.profile_image_url || "";
    resetProfileImageCrop();
    renderProfileImagePreview();
    renderMessenger();
    selectProfilePhotoButton.focus({ preventScroll: true });
    setAppStatus("프로필 사진을 1024×1024로 저장했어요.", "success");
  } catch (error) {
    if (state.profileImageSelectionId !== selectionId) return;
    const message = error?.message === "profile-image-too-large"
      ? "변환된 프로필 사진이 3MB를 넘어 저장할 수 없어요."
      : "프로필 사진을 저장하지 못했어요. 다시 시도해 주세요.";
    setAppStatus(message, "error");
  } finally {
    if (state.profileImageSelectionId === selectionId) setProfileImagePreparing(false);
  }
}

function cancelProfileImageCrop() {
  if (state.profileImagePreparing) return;
  state.profileImageSelectionId += 1;
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

async function removeProfileImage() {
  if (state.profileImagePreparing || state.profileCropOpen || !state.profileImageUrl) return;
  state.profileImageSelectionId += 1;
  setProfileImagePreparing(true);
  try {
    const data = await api("/profile/image/remove", { method: "POST" });
    state.messenger.user = data.user;
    if (state.session?.user) state.session.user = data.user;
    state.profileImageUrl = "";
    revokeProfileImagePreview();
    renderProfileImagePreview();
    renderMessenger();
    setAppStatus("프로필 사진을 제거했어요.", "success");
  } catch (error) {
    setAppStatus(error.message, "error");
  } finally {
    setProfileImagePreparing(false);
  }
}

function openProfileEditor() {
  const user = state.messenger.user || state.session?.user;
  resetProfileImageCrop();
  state.profilePixels = normalizeProfilePixels(user?.profile_pixels);
  revokeProfileImagePreview();
  state.profileImageUrl = user?.profile_image_url || "";
  state.customPalette = Array.isArray(user?.custom_palette) ? user.custom_palette : [];
  profileDisplayName.value = getDisplayName(user);
  profileFriendCode.value = user?.friend_code || "";
  state.selectedProfileColor = "#000000";
  state.selectedProfilePalette = "default";
  state.palettePickerOpen = false;
  state.lastPixelTapIndex = -1;
  customProfileColor.value = state.selectedProfileColor;
  renderProfilePaletteSelect();
  renderProfilePalette();
  renderCustomPalette();
  renderPalettePicker();
  buildProfileEditor();
  setProfileImagePreparing(state.profileImagePreparing);
  renderProfileImagePreview();
  profileSheet.classList.remove("hidden");
}

function closeProfileEditor() {
  if (state.profileImagePreparing) {
    setAppStatus("프로필 사진 처리가 끝날 때까지 잠시 기다려 주세요.");
    return;
  }
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
    const data = await api("/profile", {
      method: "POST",
      body: JSON.stringify({
        displayName: profileDisplayName.value.trim(),
        statusMessage: user?.status_message || "",
        friendCode: profileFriendCode.value.trim(),
        pixels: state.profilePixels,
      }),
    });
    state.messenger.user = data.user;
    if (state.session?.user) state.session.user = data.user;
    closeProfileEditor();
    renderMessenger();
    setAppStatus("프로필을 저장했어요.", "success");
  } catch (error) {
    setAppStatus(error.message, "error");
  } finally {
    saveProfileButton.disabled = false;
  }
}
