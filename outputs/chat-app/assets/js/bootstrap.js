"use strict";

// Event binding and application startup. Loaded last after every feature module.
async function checkSession() {
  const authEpoch = state.authEpoch;
  try {
    const session = await requestAction("auth.check-session", "/session", { headers: {} }, {
      key: "auth.session",
      policy: "join",
    });
    if (authEpoch !== state.authEpoch) return;
    window.clearTimeout(state.sessionCheckTimer);
    state.sessionCheckTimer = null;
    state.sessionCheckRetryCount = 0;
    if (session.authenticated) {
      rememberSession(session);
      await startApp();
    } else {
      showAuth();
    }
  } catch (error) {
    if (authEpoch !== state.authEpoch) return;
    setAuthStatus("로그인 상태를 확인하지 못했어요. 연결되면 자동으로 다시 확인합니다.", "error");
    const retryDelay = Math.min(30000, 1000 * (2 ** Math.min(state.sessionCheckRetryCount, 5)));
    state.sessionCheckRetryCount += 1;
    window.clearTimeout(state.sessionCheckTimer);
    state.sessionCheckTimer = window.setTimeout(checkSession, retryDelay);
  }
}

googleLoginButton.addEventListener("click", startGoogleLogin);
kakaoLoginButton.addEventListener("click", startKakaoLogin);
demoLoginButton.addEventListener("click", startDemoLogin);
loginForm.addEventListener("submit", submitLogin);
logoutButton.addEventListener("click", logout);
openLoginButton.addEventListener("click", () => showAuth());
closeChatRoomButton.addEventListener("click", closeChatRoom);
openRoomSettingsButton.addEventListener("click", openRoomSettings);
closeRoomSettingsButton.addEventListener("click", closeRoomSettings);
saveRoomSettingsButton.addEventListener("click", () => void saveRoomSettings());
selectRoomPhotoButton.addEventListener("click", () => {
  if (!state.roomSettingsBusy || state.roomImageProcessing) roomPhotoInput.click();
});
roomPhotoInput.addEventListener("change", () => {
  const file = roomPhotoInput.files?.[0];
  if (file) void uploadRoomPhoto(file);
});
removeRoomPhotoButton.addEventListener("click", () => void removeRoomPhoto());
leaveRoomButton.addEventListener("click", () => void leaveCurrentGroupRoom());
roomSettingsSheet.addEventListener("click", (event) => {
  if (event.target === roomSettingsSheet) closeRoomSettings();
});
chatMessageForm.addEventListener("submit", sendChatMessage);
chatMessageList.addEventListener("scroll", () => {
  closeMessageReadMenu();
  if (chatMessageList.scrollTop < 80) void loadOlderChatMessages();
}, { passive: true });
chatMessageList.addEventListener("contextmenu", showMessageReadMenuFromContext);
document.addEventListener("click", (event) => {
  if (!messageReadMenu.contains(event.target)) closeMessageReadMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMessageReadMenu();
});
chatAttachmentButton.addEventListener("pointerdown", (event) => {
  if (usesDirectAttachmentPicker()) return;
  event.preventDefault();
  if (state.chatAttachmentGuideTimer) {
    clearTimeout(state.chatAttachmentGuideTimer);
    state.chatAttachmentGuideTimer = null;
  }
  state.chatAttachmentDrag = { active: true, kind: "", startX: event.clientX, startY: event.clientY };
  chatAttachmentButton.setPointerCapture(event.pointerId);
  showAttachmentGuide();
});
chatAttachmentButton.addEventListener("pointermove", (event) => {
  if (state.chatAttachmentDrag.active) updateAttachmentSwipe(event);
});
chatAttachmentButton.addEventListener("pointerup", (event) => {
  if (!state.chatAttachmentDrag.active) return;
  const kind = state.chatAttachmentDrag.kind;
  if (chatAttachmentButton.hasPointerCapture(event.pointerId)) chatAttachmentButton.releasePointerCapture(event.pointerId);
  if (kind) {
    resetAttachmentSwipe();
    openAttachmentPicker(kind);
    return;
  }
  state.chatAttachmentDrag.active = false;
  chatAttachmentButton.classList.remove("swiping");
  state.chatAttachmentGuideTimer = setTimeout(resetAttachmentSwipe, 1600);
});
chatAttachmentButton.addEventListener("pointercancel", resetAttachmentSwipe);
chatAttachmentButton.addEventListener("click", (event) => {
  event.preventDefault();
  if (!usesDirectAttachmentPicker()) return;
  resetAttachmentSwipe();
  openAttachmentPicker();
});
chatAttachmentGuide.addEventListener("click", (event) => {
  const kind = event.target.closest?.("[data-kind]")?.dataset.kind;
  if (!kind) return;
  if (kind === "photo" || kind === "pdf") {
    chatAttachmentInput.accept = kind === "pdf" ? "application/pdf" : "image/*";
    chatAttachmentInput.value = "";
    return;
  }
  event.preventDefault();
  setAppStatus("준비 중인 기능이에요.");
});
chatAttachmentRemove.addEventListener("click", clearChatAttachment);
chatAttachmentInput.addEventListener("change", () => {
  const file = chatAttachmentInput.files?.[0];
  if (!file) return;
  void selectChatAttachment(file);
});
chatRoom.addEventListener("paste", handlePastedChatAttachment);
chatMessageInput.addEventListener("input", () => {
  if (state.selectedRoomId) state.chatDrafts[state.selectedRoomId] = chatMessageInput.value;
});
chatMessageInput.addEventListener("compositionend", () => {
  if (state.selectedRoomId) state.chatDrafts[state.selectedRoomId] = chatMessageInput.value;
});
shortMessageToggle.addEventListener("click", toggleShortMessages);
shortShareSend.addEventListener("click", handleContextActionPrimary);
let shortShareTouch = null;
shortShareBar.addEventListener("touchstart", (event) => {
  const touch = event.touches[0];
  if (!touch) return;
  shortShareTouch = { startX: touch.clientX, startY: touch.clientY, scrollLeft: shortShareList.scrollLeft };
}, { passive: true });
shortShareBar.addEventListener("touchmove", (event) => {
  if (!shortShareTouch) return;
  const touch = event.touches[0];
  if (!touch) return;
  event.preventDefault();
  const distanceX = touch.clientX - shortShareTouch.startX;
  const distanceY = touch.clientY - shortShareTouch.startY;
  if (Math.abs(distanceX) > Math.abs(distanceY)) {
    shortShareList.scrollLeft = shortShareTouch.scrollLeft - distanceX;
  }
}, { passive: false });
shortShareBar.addEventListener("touchend", () => { shortShareTouch = null; }, { passive: true });
shortShareBar.addEventListener("touchcancel", () => { shortShareTouch = null; }, { passive: true });
document.addEventListener("visibilitychange", updatePresence);
chatsTab.addEventListener("click", () => setActiveList("chats"));
friendsTab.addEventListener("click", () => setActiveList("friends"));
shortsTab.addEventListener("click", () => setActiveList("shorts"));
chatList.addEventListener("scroll", () => {
  if (chatList.scrollTop + chatList.clientHeight >= chatList.scrollHeight - 160) {
    void loadRoomsPage({ render: true }).catch(() => {});
  }
}, { passive: true });
friendList.addEventListener("scroll", () => {
  if (friendList.scrollTop + friendList.clientHeight >= friendList.scrollHeight - 160) {
    void loadFriendsPage({ render: true }).catch(() => {});
  }
}, { passive: true });
shortsSoundToggle.addEventListener("click", () => {
  state.youtube.soundEnabled = !state.youtube.soundEnabled;
  syncActiveShortAudio();
});
shortsView.addEventListener("scroll", () => {
  if (state.shortScrollFrame !== null) return;
  state.shortScrollFrame = requestAnimationFrame(() => {
    state.shortScrollFrame = null;
    updateShortsFromScroll();
  });
}, { passive: true });
window.addEventListener("resize", () => {
  if (state.shortResizeFrame !== null) return;
  state.shortResizeFrame = requestAnimationFrame(() => {
    state.shortResizeFrame = null;
    resizeShortWindow();
  });
}, { passive: true });
document.addEventListener("visibilitychange", handleShortVisibilityChange);
window.addEventListener("message", handleShortPlayerMessage);
openDirectoryButton.addEventListener("click", openDirectory);
closeDirectoryButton.addEventListener("click", closeDirectory);
friendCodeAddButton.addEventListener("click", () => addFriend(friendCodeInput.value));
openNewChatButton.addEventListener("click", openNewChat);
closeNewChatButton.addEventListener("click", closeNewChat);
newChatGroupName.addEventListener("input", syncNewChatCreateButton);
newChatSearch.addEventListener("input", renderNewChatMemberList);
newChatMemberList.addEventListener("change", updateNewChatMemberSelection);
createNewChatButton.addEventListener("click", () => void createNewChat());
newChatSheet.addEventListener("click", (event) => {
  if (event.target === newChatSheet) closeNewChat();
});
friendCodeInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addFriend(friendCodeInput.value);
  }
});
openProfileButton.addEventListener("click", () => void openProfileEditor());
openStatusEmojiButton.addEventListener("click", () => openStatusEmojiPicker(openStatusEmojiButton));
closeStatusEmojiButton.addEventListener("click", () => closeStatusEmojiPicker());
skipStatusEmojiButton.addEventListener("click", () => closeStatusEmojiPicker());
statusEmojiSheet.addEventListener("click", (event) => {
  if (event.target === statusEmojiSheet) closeStatusEmojiPicker();
});
statusEmojiSheet.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    closeStatusEmojiPicker();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...statusEmojiSheet.querySelectorAll("button:not(:disabled), input:not(:disabled)")]
    .filter((element) => element.getClientRects().length > 0);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});
closeProfileButton.addEventListener("click", closeProfileEditor);
selectProfilePhotoButton.addEventListener("click", () => {
  profilePhotoInput.click();
});
profilePhotoInput.addEventListener("change", () => {
  const file = profilePhotoInput.files?.[0];
  if (file) void uploadSelectedProfileImage(file);
});
profilePhotoZoom.addEventListener("input", updateProfileCropZoom);
profilePhotoCropCanvas.addEventListener("pointerdown", (event) => {
  if (state.profileImagePreparing || !state.profileCropImage) return;
  event.preventDefault();
  profilePhotoCropCanvas.focus({ preventScroll: true });
  profilePhotoCropCanvas.setPointerCapture(event.pointerId);
  state.profileCropPointer = {
    id: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    offsetX: state.profileCropOffsetX,
    offsetY: state.profileCropOffsetY,
  };
  profilePhotoCropCanvas.classList.add("dragging");
});
profilePhotoCropCanvas.addEventListener("pointermove", (event) => {
  const pointer = state.profileCropPointer;
  if (!pointer || pointer.id !== event.pointerId) return;
  event.preventDefault();
  const previewWidth = Math.max(1, profilePhotoCropCanvas.getBoundingClientRect().width);
  const outputPixelsPerClientPixel = PROFILE_IMAGE_SIDE / previewWidth;
  moveProfileCrop(
    pointer.offsetX + (event.clientX - pointer.startX) * outputPixelsPerClientPixel,
    pointer.offsetY + (event.clientY - pointer.startY) * outputPixelsPerClientPixel,
  );
});
profilePhotoCropCanvas.addEventListener("pointerup", finishProfileCropPointer);
profilePhotoCropCanvas.addEventListener("pointercancel", finishProfileCropPointer);
profilePhotoCropCanvas.addEventListener("lostpointercapture", (event) => {
  if (state.profileCropPointer?.id !== event.pointerId) return;
  state.profileCropPointer = null;
  profilePhotoCropCanvas.classList.remove("dragging");
});
profilePhotoCropCanvas.addEventListener("keydown", (event) => {
  if (!state.profileCropImage || !event.key.startsWith("Arrow")) return;
  event.preventDefault();
  const distance = event.shiftKey ? 32 : 8;
  const offsetX = state.profileCropOffsetX
    + (event.key === "ArrowLeft" ? -distance : event.key === "ArrowRight" ? distance : 0);
  const offsetY = state.profileCropOffsetY
    + (event.key === "ArrowUp" ? -distance : event.key === "ArrowDown" ? distance : 0);
  moveProfileCrop(offsetX, offsetY);
});
cancelProfilePhotoButton.addEventListener("click", cancelProfileImageCrop);
saveProfilePhotoButton.addEventListener("click", () => void saveCroppedProfileImage());
removeProfilePhotoButton.addEventListener("click", () => void removeProfileImage());
clearProfileButton.addEventListener("click", () => {
  state.profilePixels = blankProfilePixels();
  state.lastPixelTapIndex = -1;
  buildProfileEditor();
  renderProfileImagePreview();
});
profilePaletteSelect.addEventListener("change", () => {
  state.selectedProfilePalette = profilePaletteSelect.value;
  const colors = getActiveProfilePalette();
  if (colors.length) state.selectedProfileColor = colors[0];
  customProfileColor.value = state.selectedProfileColor;
  renderProfilePalette();
});

togglePalettePickerButton.addEventListener("click", () => {
  state.palettePickerOpen = !state.palettePickerOpen;
  renderPalettePicker();
});

customProfileColor.addEventListener("input", () => {
  const color = customProfileColor.value.toLowerCase();
  state.selectedProfileColor = color;
  renderProfilePalette();
  addCustomPaletteColor(color);
});
statusEmojiAdd.addEventListener("click", () => {
  profileStatusEmoji.value = "";
  profileStatusEmoji.focus({ preventScroll: true });
});
statusEmojiPicker.addEventListener("pointerdown", () => {
  state.statusPickerTouched = true;
});
statusEmojiPicker.addEventListener("touchstart", () => {
  state.statusPickerTouched = true;
}, { passive: true });
statusEmojiPicker.addEventListener("scroll", () => {
  if (!state.statusPickerTouched) return;
  window.clearTimeout(state.statusPickerTimer);
  state.statusPickerTimer = window.setTimeout(selectCenteredStatusEmoji, 150);
}, { passive: true });
profileStatusEmoji.addEventListener("input", () => {
  const emoji = normalizeStatusEmoji(profileStatusEmoji.value);
  profileStatusEmoji.value = emoji;
  if (emoji) chooseStatusEmoji(emoji);
});
saveProfileButton.addEventListener("click", saveProfilePixels);
window.addEventListener("pointerup", () => { state.profilePainting = false; });
directorySheet.addEventListener("click", (event) => {
  if (event.target === directorySheet) {
    closeDirectory();
  }
});
profileSheet.addEventListener("click", (event) => {
  if (event.target === profileSheet) {
    closeProfileEditor();
  }
});

if (window.location.protocol === "file:") {
  showAuth();
  setAuthStatus("이 파일을 직접 열면 로그인할 수 없어요. http://127.0.0.1:8765/ 주소로 열어 주세요.", "error");
} else {
  consumeAuthQuery();
  loadProviders();
  checkSession();
}
