"use strict";

// Group room metadata, room photo, and membership actions.
function isCurrentUserRoomOwner(room) {
  return Boolean(room && room.created_by === state.messenger.user?.username);
}

function setRoomSettingsBusy(isBusy) {
  state.roomSettingsBusy = isBusy;
  renderRoomSettings();
}

function renderRoomSettings() {
  const room = currentRoom();
  const isGroup = room?.kind === "group";
  const isOwner = isCurrentUserRoomOwner(room);
  if (!isGroup) {
    roomSettingsSheet.classList.add("hidden");
    return;
  }

  roomSettingsPhotoPreview.replaceChildren(createRoomAvatar(room));
  if (document.activeElement !== roomSettingsName) roomSettingsName.value = room.name;
  roomSettingsName.disabled = !isOwner || state.roomSettingsBusy;
  selectRoomPhotoButton.disabled = !isOwner || state.roomSettingsBusy;
  removeRoomPhotoButton.disabled = !isOwner || state.roomSettingsBusy || !room.image_url;
  saveRoomSettingsButton.disabled = !isOwner || state.roomSettingsBusy;
  leaveRoomButton.disabled = state.roomSettingsBusy;
  closeRoomSettingsButton.disabled = state.roomSettingsBusy;
  roomSettingsOwnerHelp.textContent = isOwner
    ? "방장은 채팅방 이름과 사진을 변경할 수 있습니다."
    : "이름과 사진은 방장만 변경할 수 있습니다.";
}

function openRoomSettings() {
  const room = currentRoom();
  if (!room || room.kind !== "group") return;
  roomSettingsName.value = room.name;
  renderRoomSettings();
  roomSettingsSheet.classList.remove("hidden");
  (isCurrentUserRoomOwner(room) ? roomSettingsName : leaveRoomButton).focus({ preventScroll: true });
}

function closeRoomSettings() {
  if (state.roomSettingsBusy) return;
  roomSettingsSheet.classList.add("hidden");
  openRoomSettingsButton.focus({ preventScroll: true });
}

function applyRoomSettingsUpdate(room) {
  const updatedRoom = upsertMessengerRoom(room);
  if (!updatedRoom) return;
  renderChatRoom();
  renderChats();
  renderRoomSettings();
}

async function saveRoomSettings() {
  const room = currentRoom();
  const name = roomSettingsName.value.trim();
  if (!room || room.kind !== "group" || !isCurrentUserRoomOwner(room)) return;
  if (!name || name.length > 32) {
    setAppStatus("채팅방 이름은 1~32자로 입력해 주세요.", "error");
    roomSettingsName.focus();
    return;
  }
  if (name === room.name) {
    setAppStatus("변경된 채팅방 이름이 없습니다.");
    return;
  }
  setRoomSettingsBusy(true);
  try {
    const data = await api("/rooms/settings", {
      method: "POST",
      body: JSON.stringify({ roomId: room.id, name }),
    });
    applyRoomSettingsUpdate(data.room);
    setAppStatus("채팅방 이름을 변경했습니다.", "success");
  } catch (error) {
    setAppStatus(error.message, "error");
  } finally {
    setRoomSettingsBusy(false);
  }
}

async function createRoomImageBundle(file) {
  const image = await decodeAttachmentImage(file, 4096);
  try {
    const { width, height } = decodedImageSize(image);
    if (!width || !height) throw new Error("image-decode-failed");
    const canvas = createImageCanvas(PROFILE_IMAGE_SIDE, PROFILE_IMAGE_SIDE);
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("image-canvas-unavailable");
    const scale = Math.max(PROFILE_IMAGE_SIDE / width, PROFILE_IMAGE_SIDE / height);
    const drawWidth = width * scale;
    const drawHeight = height * scale;
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, PROFILE_IMAGE_SIDE, PROFILE_IMAGE_SIDE);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(
      image,
      (PROFILE_IMAGE_SIDE - drawWidth) / 2,
      (PROFILE_IMAGE_SIDE - drawHeight) / 2,
      drawWidth,
      drawHeight,
    );

    let imageBlob = null;
    for (const quality of PROFILE_IMAGE_WEBP_QUALITIES) {
      const candidate = await canvasToWebpBlob(canvas, quality);
      if (candidate.type === "image/webp" && candidate.size <= PROFILE_IMAGE_UPLOAD_BYTES_MAX) {
        imageBlob = candidate;
        break;
      }
    }
    if (!imageBlob) throw new Error("room-image-too-large");

    const thumbnailCanvas = createImageCanvas(PROFILE_THUMBNAIL_SIDE, PROFILE_THUMBNAIL_SIDE);
    const thumbnailContext = thumbnailCanvas.getContext("2d", { alpha: false });
    if (!thumbnailContext) throw new Error("image-canvas-unavailable");
    thumbnailContext.imageSmoothingEnabled = true;
    thumbnailContext.imageSmoothingQuality = "high";
    thumbnailContext.drawImage(canvas, 0, 0, PROFILE_THUMBNAIL_SIDE, PROFILE_THUMBNAIL_SIDE);
    const thumbnailBlob = await canvasToWebpBlob(thumbnailCanvas, 0.8);
    const sizeHeader = new ArrayBuffer(4);
    new DataView(sizeHeader).setUint32(0, imageBlob.size, false);
    return new Blob([sizeHeader, imageBlob, thumbnailBlob], {
      type: "application/x-colorless-room-bundle",
    });
  } finally {
    image.close?.();
  }
}

async function uploadRoomPhoto(file) {
  const room = currentRoom();
  if (!room || room.kind !== "group" || !isCurrentUserRoomOwner(room)) return;
  const contentType = attachmentContentType(file);
  if (!contentType.startsWith("image/") || contentType === "image/gif") {
    setAppStatus("JPG, PNG, WebP, AVIF 또는 HEIC 사진을 선택해 주세요.", "error");
    return;
  }
  if (file.size > PROFILE_IMAGE_SOURCE_BYTES_MAX) {
    setAppStatus("채팅방 사진 원본은 50MB 이하만 선택할 수 있습니다.", "error");
    return;
  }

  const roomId = room.id;
  setRoomSettingsBusy(true);
  setAppStatus("채팅방 사진을 최적화하는 중입니다.");
  try {
    const bundle = await createRoomImageBundle(file);
    const data = await api("/rooms/image", {
      method: "POST",
      headers: { "Content-Type": bundle.type, "X-Room-Id": roomId },
      body: bundle,
    });
    if (state.selectedRoomId === roomId) applyRoomSettingsUpdate(data.room);
    setAppStatus("채팅방 사진을 변경했습니다.", "success");
  } catch (error) {
    setAppStatus("채팅방 사진을 변경하지 못했습니다. 다른 사진으로 다시 시도해 주세요.", "error");
  } finally {
    roomPhotoInput.value = "";
    setRoomSettingsBusy(false);
  }
}

async function removeRoomPhoto() {
  const room = currentRoom();
  if (!room || !room.image_url || !isCurrentUserRoomOwner(room)) return;
  setRoomSettingsBusy(true);
  try {
    const data = await api("/rooms/image/remove", {
      method: "POST",
      body: JSON.stringify({ roomId: room.id }),
    });
    applyRoomSettingsUpdate(data.room);
    setAppStatus("채팅방 사진을 삭제했습니다.", "success");
  } catch (error) {
    setAppStatus(error.message, "error");
  } finally {
    setRoomSettingsBusy(false);
  }
}

async function leaveCurrentGroupRoom() {
  const room = currentRoom();
  if (!room || room.kind !== "group") return;
  if (!window.confirm(`'${room.name}' 채팅방에서 나갈까요?`)) return;
  setRoomSettingsBusy(true);
  try {
    await api("/rooms/leave", {
      method: "POST",
      body: JSON.stringify({ roomId: room.id }),
    });
    removeMessengerRoom(room.id);
    roomSettingsSheet.classList.add("hidden");
    closeChatRoom();
    renderMessenger();
    setAppStatus(`${room.name} 채팅방에서 나갔습니다.`, "success");
  } catch (error) {
    setAppStatus(error.message, "error");
  } finally {
    setRoomSettingsBusy(false);
  }
}
