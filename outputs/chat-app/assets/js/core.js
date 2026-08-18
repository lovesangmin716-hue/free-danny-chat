"use strict";

// Shared state, DOM references, API client, and presentation primitives.
const state = {
  session: null,
  providers: {},
  messenger: { friends: [], discoverableUsers: [], rooms: [] },
  activeList: "chats",
  selectedShareRoomId: "",
  shortMessageNotice: null,
  shortMessageTimer: null,
  shortMessagePaused: false,
  shortInlineReply: null,
  shortsMessagesEnabled: localStorage.getItem("colorless-shorts-messages") !== "off",
  selectedRoomId: "",
  messages: [],
  messageIndexes: new Map(),
  messageNodes: new Map(),
  messageRevision: 0,
  renderedMessageRevision: -1,
  renderedMessageRoomId: "",
  messagesNextCursor: "",
  messagesLoadingOlder: false,
  chatDrafts: {},
  chatAttachment: null,
  chatAttachmentType: "",
  chatAttachmentPreparing: false,
  chatAttachmentUpload: null,
  chatAttachmentSelectionId: 0,
  chatAttachmentTrayOpen: false,
  chatAttachmentDrag: { active: false, kind: "" },
  chatAttachmentGuideTimer: null,
  roomSettingsBusy: false,
  eventSource: null,
  eventConnected: false,
  eventEverConnected: false,
  eventReconnectTimer: null,
  sessionCheckTimer: null,
  sessionCheckRetryCount: 0,
  appStartRetryTimer: null,
  appStartRetryCount: 0,
  liveSyncTimer: null,
  liveSyncBusy: false,
  liveSyncInitialized: false,
  lastSeenRoomMessageIds: {},
  profilePixels: "",
  profileImageUrl: "",
  profileImagePreviewUrl: "",
  profileImagePreparing: false,
  profileImageSelectionId: 0,
  profileCropImage: null,
  profileCropOpen: false,
  profileCropZoomPercent: 100,
  profileCropOffsetX: 0,
  profileCropOffsetY: 0,
  profileCropPointer: null,
  profileCropFrame: null,
selectedProfileColor: "#000000",
selectedProfilePalette: "default",
  customPalette: [],
  selectedStatusEmoji: "",
  statusPickerTouched: false,
  statusPickerTimer: null,
  authRequestBusy: false,
  youtube: {
    accessToken: "",
    videos: [],
    activeIndex: 0,
    seedTitle: "",
    message: "",
    guestVideos: [],
    guestCursor: "",
    guestLoading: false,
    guestError: "",
    feedVersion: 0,
    renderedFeedVersion: -1,
    renderedGuestVideoCount: 0,
    soundEnabled: false,
    tokenClient: null,
  },
  isGuest: false,
  palettePickerOpen: false,
lastPixelTapIndex: -1,
  profilePainting: false,
  profileCells: [],
  shortScrollFrame: null,
};
sessionStorage.removeItem("free-danny-session-token");

const ATTACHMENT_UPLOAD_BYTES_MAX = 8 * 1024 * 1024;
const IMAGE_SOURCE_BYTES_MAX = 50 * 1024 * 1024;
const IMAGE_OPTIMIZE_BYTES_MIN = 512 * 1024;
const IMAGE_EDGE_PIXELS_MAX = 2560;
const IMAGE_TOTAL_PIXELS_MAX = 32 * 1000 * 1000;
const IMAGE_WEBP_QUALITY = 0.82;
const IMAGE_REQUIRED_SAVINGS_RATIO = 0.9;
const PROFILE_IMAGE_SOURCE_BYTES_MAX = 50 * 1024 * 1024;
const PROFILE_IMAGE_UPLOAD_BYTES_MAX = 3 * 1024 * 1024;
const PROFILE_IMAGE_SIDE = 1024;
const PROFILE_THUMBNAIL_SIDE = 128;
const PROFILE_IMAGE_WEBP_QUALITIES = [0.86, 0.74, 0.62];
const PIXEL_SIDE = 32;
const PROFILE_PIXEL_COUNT = PIXEL_SIDE * PIXEL_SIDE;
const PROFILE_PIXEL_CACHE_MAX = 128;
const MAX_SHORTS_FEED_ITEMS = 200;
const profilePixelCanvasCache = new Map();
const DEFAULT_PROFILE_PALETTE = ["#ffffff", "#000000", "#777777", "#d9d9d9", "#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5", "#8e24aa", "#6d4c41", "#ec407a"];
const PROFILE_PALETTES = [
  ["#ff77b7", "#ffb3d9", "#ffe7f2", "#7cf0ff", "#2a2a3a"],
  ["#ff4d6d", "#ffb703", "#a7f432", "#39d5ff", "#1b1f2a"],
  ["#ff6b6b", "#ffa94d", "#ffd93d", "#6bcbef", "#2d2a32"],
  ["#0f172a", "#1f2a44", "#2dd4bf", "#b7f7e7", "#7c3aed"],
  ["#120b1e", "#3a0a2a", "#b80f3c", "#ff4d8d", "#f5e6ff"],
  ["#ff8fab", "#ffd6a5", "#fdffb6", "#caffbf", "#1f2937"],
  ["#0b1020", "#ff2ea6", "#7a5cff", "#25f4ff", "#f6f7fb"],
  ["#2b1d14", "#6f4e37", "#b08968", "#e6ccb2", "#fefae0"],
  ["#2a1c14", "#6b4226", "#c58c5b", "#f2d0a7", "#b86bff"],
  ["#1b263b", "#415a77", "#778da9", "#ff7aa2", "#f8f9ff"],
  ["#3b1d5a", "#ff3d3d", "#ffce3a", "#2ee59d", "#fff4d6"],
  ["#f7f0ff", "#e2d6ff", "#c7b8ff", "#ffb3c1", "#2a2a3a"],
  ["#0a0f1c", "#ffffff", "#ff2d55", "#ffd60a", "#34d1ff"],
  ["#ffb38a", "#ff7a59", "#ffd9c2", "#5ad1c7", "#1f2a44"],
  ["#0b1320", "#1f2937", "#a3ff12", "#f5ff9a", "#7c7cff"],
  ["#160a22", "#3a1d5a", "#8a4fff", "#d6c2ff", "#fff7ff"],
  ["#2a1c14", "#ff6b00", "#ffb703", "#fff1b8", "#ff3d81"],
  ["#ff5fa2", "#ff9f1c", "#fff1a8", "#4deeea", "#3a0a2a"],
  ["#0b1020", "#25304a", "#2ee59d", "#6bcbef", "#ff77b7"],
  ["#10131a", "#ff4d8d", "#7a5cff", "#c7b8ff", "#f6f7fb"],
  ["#1f2937", "#ffd60a", "#fff1b8", "#a7f432", "#ff6b6b"],
  ["#ff2d55", "#ff7a59", "#ffd9c2", "#25f4ff", "#0a0f1c"],
];
/*
const PROFILE_PALETTE_NAMES = [
  "솜사탕 아케이드", "구미 웜 글로우", "소다 팝 선셋", "민트칩 던전",
  "체리 콜라 나이트", "롤리팝 과수원", "태피 네온 골목", "캐러멜 라떼",
  "버블티 보바", "프로스티 베리", "젤리빈 바자", "마시멜로 구름나라",
  "하드캔디 대비", "피치 소다 해변", "사워 애플 가로등", "리코리스 라벤더",
  "캔디콘 카니발", "레인보우 셔벗", "록캔디 동굴", "버블검 메카",
  "허니 레몬 팝", "핑크 레모네이드",
];
const STATUS_EMOJI_OPTIONS = ["🥳", "😀", "😐", "😕", "😭"];
*/
/*
const PROFILE_PALETTE_NAMES = [
  "솜사탕 아케이드", "구미 웜 글로우", "소다 팝 선셋", "민트칩 던전",
  "체리 콜라 나이트", "롤리팝 과수원", "태피 네온 골목", "캐러멜 라떼",
  "버블티 보바", "프로스티 베리", "젤리빈 바자", "마시멜로 구름나라",
  "하드캔디 대비", "피치 소다 해변", "사워 애플 가로등", "리코리스 라벤더",
  "캔디콘 카니발", "레인보우 셔벗", "록캔디 동굴", "버블검 메카",
  "허니 레몬 팝", "핑크 레모네이드",
];
const STATUS_EMOJI_OPTIONS = ["🥳", "😀", "😐", "😕", "😭"];
*/
const PROFILE_PALETTE_NAMES = [
  "\uc1a0\uc0ac\ud0d5 \uc544\ucf00\uc774\ub4dc", "\uad6c\ubbf8 \uc6dc \uae00\ub85c\uc6b0", "\uc18c\ub2e4 \ud31d \uc120\uc14b", "\ubbfc\ud2b8\uce69 \ub358\uc804",
  "\uccb4\ub9ac \ucf5c\ub77c \ub098\uc774\ud2b8", "\ub864\ub9ac\ud31d \uacfc\uc218\uc6d0", "\ud0dc\ud53c \ub124\uc628 \uace8\ubaa9", "\uce90\ub7ec\uba5c \ub77c\ub5bc",
  "\ubc84\ube14\ud2f0 \ubcf4\ubc14", "\ud504\ub85c\uc2a4\ud2f0 \ubca0\ub9ac", "\uc824\ub9ac\ube48 \ubc14\uc790", "\ub9c8\uc2dc\uba5c\ub85c \uad6c\ub984\ub098\ub77c",
  "\ud558\ub4dc\uce94\ub514 \ub300\ube44", "\ud53c\uce58 \uc18c\ub2e4 \ud574\ubcc0", "\uc0ac\uc6cc \uc560\ud50c \uac00\ub85c\ub4f1", "\ub9ac\ucf54\ub9ac\uc2a4 \ub77c\ubca4\ub354",
  "\uce94\ub514\ucf58 \uce74\ub2c8\ubc1c", "\ub808\uc778\ubcf4\uc6b0 \uc154\ubcb3", "\ub85d\uce94\ub514 \ub3d9\uad74", "\ubc84\ube14\uac80 \uba54\uce74",
  "\ud5c8\ub2c8 \ub808\ubaac \ud31d", "\ud551\ud06c \ub808\ubaa8\ub124\uc774\ub4dc",
];
const STATUS_EMOJI_OPTIONS = ["\ud83e\udd73", "\ud83d\ude00", "\ud83d\ude10", "\ud83d\ude15", "\ud83d\ude2d"];
const emojiSegmenter = typeof Intl.Segmenter === "function"
  ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
  : null;

const authScreen = document.getElementById("auth-screen");
const appScreen = document.getElementById("app-screen");
const authStatus = document.getElementById("auth-status");
const providerStatus = document.getElementById("provider-status");
const logoutButton = document.getElementById("logout-button");
const openLoginButton = document.getElementById("open-login-button");
const appTitle = document.getElementById("app-title");
const chatsTab = document.getElementById("chats-tab");
const friendsTab = document.getElementById("friends-tab");
const shortsTab = document.getElementById("shorts-tab");
const shortsSoundToggle = document.getElementById("shorts-sound-toggle");
const chatList = document.getElementById("chat-list");
const friendList = document.getElementById("friend-list");
const chatRoom = document.getElementById("chat-room");
const closeChatRoomButton = document.getElementById("close-chat-room");
const chatRoomAvatar = document.getElementById("chat-room-avatar");
const chatRoomName = document.getElementById("chat-room-name");
const chatRoomPresence = document.getElementById("chat-room-presence");
const openRoomSettingsButton = document.getElementById("open-room-settings-button");
const roomSettingsSheet = document.getElementById("room-settings-sheet");
const closeRoomSettingsButton = document.getElementById("close-room-settings-button");
const roomSettingsPhotoPreview = document.getElementById("room-settings-photo-preview");
const roomSettingsName = document.getElementById("room-settings-name");
const roomSettingsOwnerHelp = document.getElementById("room-settings-owner-help");
const roomPhotoInput = document.getElementById("room-photo-input");
const selectRoomPhotoButton = document.getElementById("select-room-photo-button");
const removeRoomPhotoButton = document.getElementById("remove-room-photo-button");
const saveRoomSettingsButton = document.getElementById("save-room-settings-button");
const leaveRoomButton = document.getElementById("leave-room-button");
const chatMessageList = document.getElementById("chat-message-list");
const chatMessageForm = document.getElementById("chat-message-form");
const chatMessageInput = document.getElementById("chat-message-input");
const chatAttachmentInput = document.getElementById("chat-attachment-input");
const chatAttachmentButton = document.getElementById("chat-attachment-button");
const chatAttachmentTray = document.getElementById("chat-attachment-tray");
const chatAttachmentOptions = document.querySelectorAll("[data-attachment-kind]");
const chatAttachmentPuck = document.getElementById("chat-attachment-puck");
const chatAttachmentGuide = document.getElementById("chat-attachment-guide");
const chatAttachmentGuideItems = chatAttachmentGuide.querySelectorAll("[data-kind]");
const chatAttachmentPreview = document.getElementById("chat-attachment-preview");
const chatAttachmentName = document.getElementById("chat-attachment-name");
const chatAttachmentRemove = document.getElementById("chat-attachment-remove");
const shortsView = document.getElementById("shorts-view");
const shortsFeed = document.getElementById("shorts-feed");
const shortShareBar = document.getElementById("short-share-bar");
const shortShareList = document.getElementById("short-share-list");
const shortMessageToggle = document.getElementById("short-message-toggle");
const shortShareSend = document.getElementById("short-share-send");
const shortShareFeedback = document.getElementById("short-share-feedback");
const appStatus = document.getElementById("app-status");
const openNewChatButton = document.getElementById("open-new-chat-button");
const closeNewChatButton = document.getElementById("close-new-chat-button");
const newChatSheet = document.getElementById("new-chat-sheet");
const newChatMemberList = document.getElementById("new-chat-member-list");
const newChatGroupNameField = document.getElementById("new-chat-group-name-field");
const newChatGroupName = document.getElementById("new-chat-group-name");
const createNewChatButton = document.getElementById("create-new-chat-button");
const openDirectoryButton = document.getElementById("open-directory-button");
const closeDirectoryButton = document.getElementById("close-directory-button");
const directorySheet = document.getElementById("directory-sheet");
const userDirectory = document.getElementById("user-directory");
const friendCodeInput = document.getElementById("friend-code-input");
const friendCodeAddButton = document.getElementById("friend-code-add-button");
const openProfileButton = document.getElementById("open-profile-button");
const closeProfileButton = document.getElementById("close-profile-button");
const profileSheet = document.getElementById("profile-sheet");
const pixelPalette = document.getElementById("pixel-palette");
const profilePaletteSelect = document.getElementById("profile-palette-select");
const customProfileColor = document.getElementById("custom-profile-color");
const customColorButton = document.getElementById("custom-color-button");
const customPaletteColors = document.getElementById("custom-palette-colors");
const customPaletteCount = document.getElementById("custom-palette-count");
const customPaletteEmpty = document.getElementById("custom-palette-empty");
const togglePalettePickerButton = document.getElementById("toggle-palette-picker-button");
const palettePicker = document.getElementById("palette-picker");
const pixelEditorGrid = document.getElementById("pixel-editor-grid");
const profileDisplayName = document.getElementById("profile-display-name");
const profileFriendCode = document.getElementById("profile-friend-code");
const profilePhotoPreview = document.getElementById("profile-photo-preview");
const profilePhotoInput = document.getElementById("profile-photo-input");
const selectProfilePhotoButton = document.getElementById("select-profile-photo-button");
const removeProfilePhotoButton = document.getElementById("remove-profile-photo-button");
const profilePhotoCrop = document.getElementById("profile-photo-crop");
const profilePhotoCropCanvas = document.getElementById("profile-photo-crop-canvas");
const profilePhotoZoom = document.getElementById("profile-photo-zoom");
const profilePhotoZoomValue = document.getElementById("profile-photo-zoom-value");
const cancelProfilePhotoButton = document.getElementById("cancel-profile-photo-button");
const saveProfilePhotoButton = document.getElementById("save-profile-photo-button");
const statusEmojiPicker = document.getElementById("status-emoji-picker");
const profileStatusEmoji = document.getElementById("profile-status-emoji");
const statusEmojiAdd = document.getElementById("status-emoji-add");
const statusEmojiSheet = document.getElementById("status-emoji-sheet");
const clearProfileButton = document.getElementById("clear-profile-button");
const saveProfileButton = document.getElementById("save-profile-button");
const loginForm = document.getElementById("login-form");
const loginSubmitButton = document.getElementById("login-submit-button");
const googleLoginButton = document.getElementById("google-login-button");
const googleButtonContainer = document.getElementById("google-button-container");
const kakaoLoginButton = document.getElementById("kakao-login-button");
const demoLoginButton = document.getElementById("demo-login-button");
const loginUsername = document.getElementById("login-username");
const loginPassword = document.getElementById("login-password");

function normalizeStatusEmoji(value) {
  const trimmed = (value || "").trim();
  const emoji = emojiSegmenter
    ? [...emojiSegmenter.segment(trimmed)][0]?.segment || ""
    : Array.from(trimmed)[0] || "";
  return /[\p{Extended_Pictographic}\p{Regional_Indicator}]/u.test(emoji) ? emoji : "";
}

function renderStatusEmojiPicker() {
  statusEmojiPicker.replaceChildren();
  STATUS_EMOJI_OPTIONS.forEach((emoji) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `status-emoji-button${state.selectedStatusEmoji === emoji ? " active" : ""}`;
    button.dataset.emoji = emoji;
    button.textContent = emoji;
    button.setAttribute("aria-label", "상태 이모티콘 선택");
    button.addEventListener("click", () => chooseStatusEmoji(emoji));
    statusEmojiPicker.appendChild(button);
  });
  statusEmojiPicker.appendChild(statusEmojiAdd);
}

function openStatusEmojiPicker() {
  state.selectedStatusEmoji = "";
  state.statusPickerTouched = false;
  profileStatusEmoji.value = "";
  renderStatusEmojiPicker();
  statusEmojiSheet.classList.remove("hidden");
}

function selectCenteredStatusEmoji() {
  if (!state.statusPickerTouched || statusEmojiSheet.classList.contains("hidden")) return;

  const pickerBounds = statusEmojiPicker.getBoundingClientRect();
  const centerY = pickerBounds.top + pickerBounds.height / 2;
  const choices = [...statusEmojiPicker.querySelectorAll(".status-emoji-button, .status-emoji-add")];
  const centeredChoice = choices.reduce((closest, choice) => {
    const bounds = choice.getBoundingClientRect();
    const distance = Math.abs(bounds.top + bounds.height / 2 - centerY);
    return !closest || distance < closest.distance ? { choice, distance } : closest;
  }, null)?.choice;

  if (!centeredChoice) return;
  if (centeredChoice === statusEmojiAdd) {
    profileStatusEmoji.value = "";
    profileStatusEmoji.focus({ preventScroll: true });
    return;
  }
  chooseStatusEmoji(centeredChoice.dataset.emoji);
}

async function chooseStatusEmoji(emoji) {
  const user = state.messenger.user || state.session?.user;
  if (!user || !emoji) return;

  state.selectedStatusEmoji = emoji;
  profileStatusEmoji.value = emoji;
  renderStatusEmojiPicker();

  try {
    const data = await api("/profile", {
      method: "POST",
      body: JSON.stringify({
        displayName: getDisplayName(user),
        statusMessage: emoji,
        friendCode: user.friend_code,
        pixels: normalizeProfilePixels(user.profile_pixels),
      }),
    });
    state.messenger.user = data.user;
    if (state.session?.user) state.session.user = data.user;
    statusEmojiSheet.classList.add("hidden");
    renderMessenger();
    updatePresence();
  } catch (error) {
    setAppStatus(error.message, "error");
  }
}

function setAuthStatus(message, tone = "default") {
  authStatus.textContent = message;
  authStatus.className = `auth-status${tone === "default" ? "" : ` ${tone}`}`;
}

function setProviderStatus(message, tone = "default") {
  providerStatus.textContent = message;
  providerStatus.className = `provider-status${tone === "default" ? "" : ` ${tone}`}`;
}

function setAppStatus(message, tone = "default") {
  appStatus.textContent = message;
  appStatus.className = `app-status${tone === "default" ? "" : ` ${tone}`}`;
}

let layoutViewportHeight = Math.max(window.innerHeight, document.documentElement.clientHeight);

function syncKeyboardInset() {
  const visualViewport = window.visualViewport;
  const isTextField = document.activeElement?.matches("input:not([type=file]), textarea");
  if (!visualViewport || !isTextField || visualViewport.height < 200) {
    document.documentElement.style.setProperty("--keyboard-inset", "0px");
    return;
  }
  const keyboardInset = Math.max(0, Math.round(layoutViewportHeight - visualViewport.height - visualViewport.offsetTop));
  const safeInset = keyboardInset > 110
    ? Math.min(keyboardInset, Math.max(0, layoutViewportHeight - 280))
    : 0;
  document.documentElement.style.setProperty("--keyboard-inset", `${safeInset}px`);
}

window.visualViewport?.addEventListener("resize", syncKeyboardInset);
window.visualViewport?.addEventListener("scroll", syncKeyboardInset);
document.addEventListener("focusin", () => window.setTimeout(syncKeyboardInset, 0));
document.addEventListener("focusout", () => window.setTimeout(syncKeyboardInset, 120));
window.addEventListener("orientationchange", () => window.setTimeout(() => {
  layoutViewportHeight = Math.max(window.innerHeight, document.documentElement.clientHeight);
  syncKeyboardInset();
}, 180));

async function api(url, options = {}) {
  const { headers: optionHeaders = {}, ...requestOptions } = options;
  const isJsonBody = typeof requestOptions.body === "string";
  const method = String(requestOptions.method || "GET").toUpperCase();
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: {
      ...(isJsonBody ? { "Content-Type": "application/json" } : {}),
      ...optionHeaders,
    },
    ...requestOptions,
  });
  const contentType = response.headers.get("Content-Type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
    try {
      payload = await response.json();
    } catch (_) {
      payload = null;
    }
  }
  const statusLabel = `${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;
  const fallbackMessage = `${method} ${url} 요청 실패 (HTTP ${statusLabel})`;

  if (response.status === 401) {
    showAuth();
    const error = new Error(payload?.error || fallbackMessage);
    error.status = response.status;
    throw error;
  }
  if (!response.ok) {
    const error = new Error(payload?.error || fallbackMessage);
    error.status = response.status;
    error.retryAfter = Number(response.headers.get("Retry-After") || 0);
    throw error;
  }
  return payload;
}

function rememberSession(session) {
  state.session = session;
  state.isGuest = false;
}

function setAuthRequestBusy(isBusy, message = "") {
  state.authRequestBusy = isBusy;
  loginSubmitButton.disabled = isBusy;
  loginSubmitButton.textContent = isBusy ? "로그인 중..." : "로그인하기";
  googleLoginButton.disabled = isBusy || !state.providers.google?.enabled;
  kakaoLoginButton.disabled = isBusy || !state.providers.kakao?.enabled;
  demoLoginButton.disabled = isBusy || !state.providers.demo?.enabled;
  googleButtonContainer.style.pointerEvents = isBusy ? "none" : "";
  googleButtonContainer.setAttribute("aria-busy", String(isBusy));
  if (message) setAuthStatus(message);
}

function beginAuthRequest(message) {
  if (state.authRequestBusy) return false;
  setAuthRequestBusy(true, message);
  return true;
}

function showAuth(mode = "login") {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  state.eventConnected = false;
  state.eventEverConnected = false;
  window.clearTimeout(state.eventReconnectTimer);
  state.eventReconnectTimer = null;
  window.clearTimeout(state.sessionCheckTimer);
  state.sessionCheckTimer = null;
  state.sessionCheckRetryCount = 0;
  window.clearTimeout(state.appStartRetryTimer);
  state.appStartRetryTimer = null;
  state.appStartRetryCount = 0;
  window.clearInterval(state.liveSyncTimer);
  state.liveSyncTimer = null;
  state.liveSyncBusy = false;
  state.liveSyncInitialized = false;
  state.lastSeenRoomMessageIds = {};
  state.selectedRoomId = "";
  state.messages = [];
  state.messageIndexes.clear();
  state.messageNodes.clear();
  state.messageRevision += 1;
  state.renderedMessageRevision = -1;
  state.renderedMessageRoomId = "";
  state.messagesNextCursor = "";
  state.messagesLoadingOlder = false;
  state.chatAttachmentUpload = null;
  state.profileImageSelectionId += 1;
  state.profileImagePreparing = false;
  state.profileImageUrl = "";
  setAuthRequestBusy(false);
  revokeProfileImagePreview();
  resetProfileImageCrop();
  state.session = null;
  state.isGuest = false;
  authScreen.classList.remove("hidden");
  appScreen.classList.add("hidden");
  directorySheet.classList.add("hidden");
  setAuthMode(mode);
}

function showApp() {
  authScreen.classList.add("hidden");
  appScreen.classList.remove("hidden");
}

function getInitial(value) {
  return (value || "?").trim().slice(0, 1).toUpperCase();
}

function getDisplayName(user) {
  return user?.display_name || user?.username || "";
}

function formatTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
}

function blankProfilePixels() {
  return Array(PROFILE_PIXEL_COUNT).fill("#ffffff");
}

function normalizeProfilePixels(pixels) {
  return Array.isArray(pixels)
    && pixels.length === PROFILE_PIXEL_COUNT
    && pixels.every((color) => typeof color === "string" && /^#[0-9a-f]{6}$/i.test(color))
    ? pixels.map((color) => color.toLowerCase())
    : blankProfilePixels();
}

function drawProfilePixels(canvas, pixels) {
  const context = canvas.getContext("2d");
  const normalizedPixels = normalizeProfilePixels(pixels);
  for (let index = 0; index < PROFILE_PIXEL_COUNT; index += 1) {
    context.fillStyle = normalizedPixels[index];
    context.fillRect(index % PIXEL_SIDE, Math.floor(index / PIXEL_SIDE), 1, 1);
  }
}

function createPixelAvatar(value, pixels) {
  const avatar = document.createElement("canvas");
  avatar.className = "avatar";
  avatar.width = PIXEL_SIDE;
  avatar.height = PIXEL_SIDE;
  avatar.setAttribute("aria-label", `profile-${value}`);
  const cacheKey = Array.isArray(pixels) ? pixels.join("") : "blank";
  let cachedCanvas = profilePixelCanvasCache.get(cacheKey);
  if (!cachedCanvas) {
    cachedCanvas = document.createElement("canvas");
    cachedCanvas.width = PIXEL_SIDE;
    cachedCanvas.height = PIXEL_SIDE;
    drawProfilePixels(cachedCanvas, pixels);
    profilePixelCanvasCache.set(cacheKey, cachedCanvas);
    if (profilePixelCanvasCache.size > PROFILE_PIXEL_CACHE_MAX) {
      profilePixelCanvasCache.delete(profilePixelCanvasCache.keys().next().value);
    }
  }
  avatar.getContext("2d").drawImage(cachedCanvas, 0, 0);
  return avatar;
}

function createAvatar(value, pixels, presence = null, savedStatus = "", profileImageUrl = "") {
  const wrapper = document.createElement("span");
  wrapper.className = "avatar-wrap";
  const avatar = profileImageUrl ? document.createElement("img") : createPixelAvatar(value, pixels);
  if (profileImageUrl) {
    avatar.className = "avatar avatar-photo";
    avatar.src = profileImageUrl;
    avatar.alt = `${value} 프로필`;
    avatar.decoding = "async";
    avatar.loading = "lazy";
    avatar.addEventListener("error", () => {
      avatar.replaceWith(createPixelAvatar(value, pixels));
    }, { once: true });
  }
  wrapper.appendChild(avatar);
  const activityEmoji = presence?.online
    ? (normalizeStatusEmoji(presence.emoji) || normalizeStatusEmoji(savedStatus))
    : "";
  if (activityEmoji) {
    const emoji = document.createElement("span");
    emoji.className = "presence-emoji";
    emoji.textContent = activityEmoji; /*
    emoji.setAttribute("aria-label", "활동 중");
    */ emoji.setAttribute("aria-label", "active");
    wrapper.appendChild(emoji);
  }
  return wrapper;
}
