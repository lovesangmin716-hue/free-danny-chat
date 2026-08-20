"use strict";

// Shorts feed paging, iframe lifecycle, sharing, and inline replies.
function youtubeDurationSeconds(duration) {
  const match = /^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(duration || "");
  if (!match) return 0;
  return Number(match[1] || 0) * 3600 + Number(match[2] || 0) * 60 + Number(match[3] || 0);
}

function createShortFrame(video) {
  const frame = document.createElement("iframe");
  frame.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share";
  frame.allowFullscreen = true;
  frame.loading = "lazy";
  frame.referrerPolicy = "strict-origin-when-cross-origin";
  frame.tabIndex = -1;
  frame.dataset.videoId = video.id;
  frame.dataset.src = shortEmbedSource(video.id);
  frame.dataset.playerReady = "false";
  frame.dataset.soundEnabled = "false";
  frame.addEventListener("load", () => beginShortPlayerHandshake(frame), { once: true });
  frame.title = video.title || "YouTube 쇼츠";
  return frame;
}

function createShortCard(video, copy, action, videoIndex) {
  const card = document.createElement("article");
  card.className = "short-card";
  card.dataset.videoIndex = String(videoIndex);

  const metadata = document.createElement("div");
  metadata.className = "short-card-copy";
  const title = document.createElement("h2");
  title.textContent = video.title || "YouTube 쇼츠";
  const description = document.createElement("p");
  description.textContent = copy;
  metadata.append(title, description);

  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action.label;
    button.addEventListener("click", action.onClick);
    metadata.appendChild(button);
  }

  card.append(metadata);
  return card;
}

function activeShortVideo() {
  return state.youtube.guestVideos[state.youtube.activeIndex] || null;
}

function clearShortMessageNotice() {
  window.clearTimeout(state.shortMessageTimer);
  state.shortMessageTimer = null;
  state.shortMessageNotice = null;
  state.shortMessagePaused = false;
  state.shortInlineReply = null;
}

function scheduleShortMessageNotice() {
  window.clearTimeout(state.shortMessageTimer);
  if (!state.shortMessageNotice || state.shortMessagePaused) return;
  state.shortMessageTimer = window.setTimeout(() => {
    clearShortMessageNotice();
    renderShortShareBar();
  }, 5000);
}

function showShortMessageNotice(room, message) {
  if (!state.shortsMessagesEnabled) return;
  state.shortMessageNotice = { roomId: room.id, sender: room.name, text: message.text };
  state.shortMessagePaused = false;
  renderShortShareBar();
  scheduleShortMessageNotice();
}

function dismissShortInlineReply() {
  if (!state.shortInlineReply) return;
  state.shortInlineReply = null;
  state.shortMessageNotice = null;
  state.shortMessagePaused = false;
  window.clearTimeout(state.shortMessageTimer);
  state.shortMessageTimer = null;
  renderShortShareBar(true);
}

function toggleShortMessages() {
  state.shortsMessagesEnabled = !state.shortsMessagesEnabled;
  localStorage.setItem("colorless-shorts-messages", state.shortsMessagesEnabled ? "on" : "off");
  if (!state.shortsMessagesEnabled && !state.shortInlineReply) clearShortMessageNotice();
  renderShortShareBar(true);
}

function pauseShortMessageNotice() {
  if (!state.shortMessageNotice) return;
  state.shortMessagePaused = true;
  window.clearTimeout(state.shortMessageTimer);
  state.shortMessageTimer = null;
}

function resumeShortMessageNotice() {
  if (!state.shortMessageNotice || !state.shortMessagePaused) return;
  state.shortMessagePaused = false;
  renderShortShareBar();
  scheduleShortMessageNotice();
}

function renderShortShareBar(force = false) {
  if (state.shortInlineReply && !force) return;
  shortShareBar.classList.toggle("replying", Boolean(state.shortInlineReply));
  shortMessageToggle.classList.toggle("enabled", state.shortsMessagesEnabled);
  shortMessageToggle.setAttribute("aria-pressed", String(state.shortsMessagesEnabled));
  shortMessageToggle.setAttribute("aria-label", state.shortsMessagesEnabled ? "쇼츠 메시지 알림 켜짐" : "쇼츠 메시지 알림 꺼짐");
  shortShareList.replaceChildren();
  shortShareFeedback.textContent = "";
  shortMessageToggle.classList.add("hidden");
  shortShareSend.classList.remove("hidden");
  ColorlessPlatform.decorateIconButton(shortShareSend, "send", { label: "쇼츠 보내기", iconOnly: true });
  if (state.shortInlineReply) {
    shortShareBar.setAttribute("aria-label", "빠른 답장 입력");
    const input = document.createElement("input");
    input.type = "text";
    input.className = "short-inline-reply";
    input.maxLength = 300;
    input.autocomplete = "off";
    input.placeholder = "빠른 답장";
    input.setAttribute("aria-label", "빠른 답장 메시지");
    input.value = state.shortInlineReply.draft || "";
    const saveDraft = () => {
      if (!state.shortInlineReply) return;
      state.shortInlineReply.draft = input.value;
      shortShareSend.disabled = !input.value.trim();
      if (!input.value.trim()) dismissShortInlineReply();
    };
    input.addEventListener("input", saveDraft);
    input.addEventListener("compositionend", saveDraft);
    input.addEventListener("keyup", saveDraft);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dismissShortInlineReply();
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        sendShortInlineReply();
      }
    });
    input.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (state.shortInlineReply && !input.value.trim()) dismissShortInlineReply();
      }, 0);
    });
    shortShareList.appendChild(input);
    shortShareSend.disabled = !input.value.trim();
    ColorlessPlatform.decorateIconButton(shortShareSend, "send", { label: "답장 보내기", iconOnly: true });
    requestAnimationFrame(() => input.focus());
    return;
  }
  if (state.shortMessageNotice) {
    shortShareBar.setAttribute("aria-label", "새 메시지 빠른 답장");
    const room = state.messenger.rooms.find((candidate) => candidate.id === state.shortMessageNotice.roomId);
    const notice = document.createElement("button");
    notice.type = "button";
    notice.className = "short-message-notice";
    notice.appendChild(createAvatar(
      state.shortMessageNotice.sender,
      room?.peer?.profile_pixels,
      room?.peer?.presence,
      room?.peer?.status_message,
      room?.peer?.profile_image_url,
    ));
    const copy = document.createElement("span");
    copy.className = "short-message-copy";
    const sender = document.createElement("strong");
    sender.textContent = state.shortMessageNotice.sender;
    const text = document.createElement("span");
    text.textContent = state.shortMessageNotice.text;
    copy.append(sender, text);
    notice.appendChild(copy);
    notice.addEventListener("click", replyToShortMessageNotice);
    shortShareList.appendChild(notice);
    shortShareSend.disabled = false;
    ColorlessPlatform.decorateIconButton(shortShareSend, "send", { label: "답장하기", iconOnly: true });
    return;
  }
  if (state.activeList === "chats") {
    renderChatActionBar();
    return;
  }
  if (state.activeList === "friends") {
    renderFriendActionBar();
    return;
  }
  shortShareBar.setAttribute("aria-label", "쇼츠 공유");
  shortMessageToggle.classList.remove("hidden");
  const rooms = recentChatRooms();
  if (!rooms.length) {
    state.selectedShareRoomIds = [];
    shortShareSend.disabled = true;
    /*
    const empty = document.createElement("p");
    empty.className = "short-share-empty";
    empty.textContent = "대화를 시작하면 여기서 쇼츠를 보낼 수 있어요.";
    shortShareList.appendChild(empty);
    */ return;
  }
  const roomIds = new Set(rooms.map((room) => room.id));
  state.selectedShareRoomIds = state.selectedShareRoomIds.filter((roomId) => roomIds.has(roomId));

  rooms.forEach((room) => {
    const person = document.createElement("button");
    person.type = "button";
    person.className = "short-share-person";
    person.dataset.roomId = room.id;
    /*
    person.title = `${room.name}에게 현재 쇼츠 보내기`;
    */ person.title = `Share to ${room.name}`;
    person.appendChild(createRoomAvatar(room));
    const name = document.createElement("span");
    name.textContent = room.name;
    person.appendChild(name);
    const isSelected = state.selectedShareRoomIds.includes(room.id);
    person.classList.toggle("selected", isSelected);
    person.setAttribute("aria-pressed", String(isSelected));
    person.addEventListener("click", () => selectShortShareRoom(room.id));
    shortShareList.appendChild(person);
  });
  const selectedCount = state.selectedShareRoomIds.length;
  shortShareSend.disabled = selectedCount === 0;
  shortShareSend.setAttribute("aria-label", selectedCount ? `선택한 ${selectedCount}개 채팅방에 쇼츠 보내기` : "쇼츠 보내기");
}

function selectShortShareRoom(roomId) {
  state.selectedShareRoomIds = state.selectedShareRoomIds.includes(roomId)
    ? state.selectedShareRoomIds.filter((selectedId) => selectedId !== roomId)
    : [...state.selectedShareRoomIds, roomId];
  renderShortShareBar();
}

async function sendSelectedShort() {
  const selectedIds = new Set(state.selectedShareRoomIds);
  const rooms = state.messenger.rooms.filter((room) => selectedIds.has(room.id));
  if (rooms.length) await shareShortToRooms(rooms);
}

async function replyToShortMessageNotice() {
  const roomId = state.shortMessageNotice?.roomId;
  if (!roomId) return;
  pauseShortMessageNotice();
  state.shortInlineReply = { roomId, draft: "" };
  renderShortShareBar(true);
}

async function sendShortInlineReply() {
  const roomId = state.shortInlineReply?.roomId;
  const input = shortShareList.querySelector(".short-inline-reply");
  if (input && state.shortInlineReply) state.shortInlineReply.draft = input.value;
  const text = (input?.value || state.shortInlineReply?.draft || "").trim();
  if (!roomId || !text) return;
  input.disabled = true;
  shortShareSend.disabled = true;
  try {
    await requestAction("shorts.reply", "/messages", { method: "POST", body: JSON.stringify({ roomId, text }) });
    clearShortMessageNotice();
    await loadMessenger(false);
    renderShortShareBar();
  } catch (error) {
    input.disabled = false;
    shortShareSend.disabled = false;
    shortShareFeedback.textContent = error.message;
  }
}

async function handleShortShareAction() {
  if (state.shortInlineReply) {
    await sendShortInlineReply();
    return;
  }
  if (state.shortMessageNotice) {
    await replyToShortMessageNotice();
    return;
  }
  await sendSelectedShort();
}

async function shareShortToRooms(rooms) {
  const video = activeShortVideo();
  if (!video) {
    shortShareFeedback.textContent = "공유할 쇼츠를 먼저 불러와 주세요.";
    return;
  }
  const shareUrl = `https://www.youtube.com/shorts/${video.id}`;
  shortShareFeedback.textContent = `${rooms.length}개 채팅방에 보내는 중…`;
  shortShareSend.disabled = true;
  try {
    await Promise.all(rooms.map((room) => requestAction(`shorts.share.${room.id}`, "/messages", {
      method: "POST",
      body: JSON.stringify({ roomId: room.id, text: shareUrl }),
    })));
    state.selectedShareRoomIds = [];
    setAppStatus(`${rooms.length}개 채팅방에 쇼츠를 보냈어요.`, "success");
    renderShortShareBar(true);
  } catch (error) {
    shortShareFeedback.textContent = error.message;
    shortShareSend.disabled = false;
  }
}

function shortEmbedSource(videoId) {
  const params = new URLSearchParams({
    autoplay: "0",
    mute: "1",
    playsinline: "1",
    rel: "0",
    enablejsapi: "1",
    origin: window.location.origin,
    widget_referrer: window.location.href,
  });
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?${params}`;
}

function sendShortPlayerCommand(frame, command, args = []) {
  frame.contentWindow?.postMessage(JSON.stringify({
    event: "command",
    func: command,
    args,
  }), "*");
}

function beginShortPlayerHandshake(frame) {
  [0, 120, 500].forEach((delay) => {
    window.setTimeout(() => {
      if (!frame.isConnected || frame.dataset.playerReady === "true") return;
      frame.contentWindow?.postMessage(JSON.stringify({ event: "listening", id: frame.dataset.videoId }), "*");
    }, delay);
  });
}

function applyShortPlayerState(frame) {
  if (frame.dataset.playerReady !== "true") return;
  const shouldPlay = frame.dataset.playbackState === "playing";
  const playbackToken = frame.dataset.playbackToken || "0";
  sendShortPlayerCommand(frame, "mute");
  sendShortPlayerCommand(frame, shouldPlay ? "playVideo" : "pauseVideo");
  if (!shouldPlay || frame.dataset.soundEnabled !== "true") return;
  window.setTimeout(() => {
    if (
      !frame.isConnected
      || frame.dataset.playerReady !== "true"
      || frame.dataset.playbackToken !== playbackToken
      || frame.dataset.playbackState !== "playing"
      || !state.youtube.soundEnabled
    ) return;
    sendShortPlayerCommand(frame, "unMute");
    sendShortPlayerCommand(frame, "setVolume", [100]);
  }, 250);
}

function handleShortPlayerMessage(event) {
  if (!["https://www.youtube-nocookie.com", "https://www.youtube.com"].includes(event.origin)) return;
  let payload = event.data;
  if (typeof payload === "string") {
    try { payload = JSON.parse(payload); } catch { return; }
  }
  if (payload?.event !== "onReady") return;
  const frame = Array.from(shortsFeed.querySelectorAll("iframe"))
    .find((candidate) => candidate.contentWindow === event.source);
  if (!frame) return;
  frame.dataset.playerReady = "true";
  applyShortPlayerState(frame);
}

function loadShortFrame(frame) {
  if (!frame.hasAttribute("src") && frame.dataset.src) {
    frame.src = frame.dataset.src;
    delete frame.dataset.src;
  }
}

function playShortFrame(frame, soundEnabled) {
  loadShortFrame(frame);
  if (!frame.hasAttribute("src")) return;

  frame.dataset.playbackToken = String((Number(frame.dataset.playbackToken) || 0) + 1);
  frame.dataset.soundEnabled = String(soundEnabled);
  frame.dataset.playbackState = "playing";
  beginShortPlayerHandshake(frame);
  applyShortPlayerState(frame);
}

function pauseShortFrame(frame) {
  if (!frame.hasAttribute("src") || frame.dataset.playbackState === "paused") return;
  frame.dataset.playbackToken = String((Number(frame.dataset.playbackToken) || 0) + 1);
  frame.dataset.playbackState = "paused";
  frame.dataset.soundEnabled = "false";
  applyShortPlayerState(frame);
}

function removeShortFrame(card) {
  const frame = card.querySelector("iframe");
  if (!frame) return;
  pauseShortFrame(frame);
  frame.removeAttribute("src");
  frame.remove();
}

function ensureShortFrame(card) {
  let frame = card.querySelector("iframe");
  if (frame) return frame;
  const videoIndex = Number(card.dataset.videoIndex);
  const video = state.youtube.guestVideos[videoIndex];
  if (!video) return null;
  frame = createShortFrame(video);
  card.prepend(frame);
  return frame;
}

function releaseAllShortFrames() {
  window.clearTimeout(state.shortSnapTimer);
  state.shortSnapTimer = null;
  shortsFeed.querySelectorAll(".short-card").forEach(removeShortFrame);
}

function syncActiveShortAudio() {
  if (document.hidden || state.activeList !== "shorts") {
    releaseAllShortFrames();
    return;
  }
  const cards = Array.from(shortsFeed.querySelectorAll(".short-card:not(.short-empty-card)"));
  cards.forEach((card) => {
    const cardIndex = Number(card.dataset.videoIndex);
    const distance = Math.abs(cardIndex - state.youtube.activeIndex);
    if (distance > 1) {
      removeShortFrame(card);
      return;
    }
    const frame = ensureShortFrame(card);
    if (!frame) return;
    loadShortFrame(frame);
    if (cardIndex === state.youtube.activeIndex) {
      const enableSound = state.youtube.soundEnabled;
      if (frame.dataset.playbackState !== "playing" || frame.dataset.soundEnabled !== String(enableSound)) {
        playShortFrame(frame, enableSound);
      }
    } else {
      pauseShortFrame(frame);
    }
  });
  ColorlessPlatform.decorateIconButton(
    shortsSoundToggle,
    state.youtube.soundEnabled ? "volume-2" : "volume-x",
    { label: state.youtube.soundEnabled ? "쇼츠 소리 끄기" : "쇼츠 소리 켜기", iconOnly: true },
  );
}

function loadVisibleShortFrames() {
  syncActiveShortAudio();
}

function handleShortVisibilityChange() {
  if (document.hidden || state.activeList !== "shorts") {
    releaseAllShortFrames();
    return;
  }
  renderShortWindow();
  syncActiveShortAudio();
}

function createShortEmptyCard(titleText, copyText, action, videoIndex = 0) {
  const card = document.createElement("article");
  card.className = "short-card short-empty-card";
  card.dataset.videoIndex = String(videoIndex);
  const title = document.createElement("h2");
  title.textContent = titleText;
  const copy = document.createElement("p");
  copy.textContent = copyText;
  card.append(title, copy);
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action.label;
    button.addEventListener("click", action.onClick);
    card.appendChild(button);
  }
  return card;
}

function shortViewportHeight() {
  return Math.max(shortsView.clientHeight, 1);
}

function shortLogicalItemCount() {
  return state.youtube.guestVideos.length + (state.youtube.guestError ? 1 : 0);
}

function shortVirtualRange(activeIndex, itemCount) {
  const windowSize = Math.min(SHORTS_DOM_WINDOW_SIZE, itemCount);
  const start = Math.min(
    Math.max(0, activeIndex - Math.floor(windowSize / 2)),
    Math.max(0, itemCount - windowSize),
  );
  return { start, end: start + windowSize };
}

function positionShortCard(card, videoIndex, height) {
  card.style.height = `${height}px`;
  card.style.left = "0";
  card.style.position = "absolute";
  card.style.right = "0";
  card.style.top = `${videoIndex * height}px`;
}

function insertShortCardInOrder(card, videoIndex) {
  const nextCard = Array.from(shortsFeed.querySelectorAll(".short-card[data-video-index]"))
    .find((candidate) => Number(candidate.dataset.videoIndex) > videoIndex);
  shortsFeed.insertBefore(card, nextCard || null);
}

function renderShortWindow(force = false) {
  const itemCount = shortLogicalItemCount();
  if (!itemCount) return;
  const height = shortViewportHeight();
  const activeIndex = Math.max(0, Math.min(state.youtube.activeIndex, itemCount - 1));
  state.youtube.activeIndex = activeIndex;
  const range = shortVirtualRange(activeIndex, itemCount);
  const unchanged = (
    !force
    && state.youtube.virtualStart === range.start
    && state.youtube.virtualEnd === range.end
    && state.youtube.virtualHeight === height
  );
  shortsFeed.style.height = `${itemCount * height}px`;
  if (unchanged) {
    syncActiveShortAudio();
    return;
  }

  const preservedScrollTop = shortsView.scrollTop;
  const existing = new Map(
    Array.from(shortsFeed.querySelectorAll(".short-card[data-video-index]"))
      .map((card) => [Number(card.dataset.videoIndex), card]),
  );
  existing.forEach((card, videoIndex) => {
    if (videoIndex < range.start || videoIndex >= range.end) {
      removeShortFrame(card);
      card.remove();
      existing.delete(videoIndex);
    }
  });
  for (let videoIndex = range.start; videoIndex < range.end; videoIndex += 1) {
    let card = existing.get(videoIndex);
    const video = state.youtube.guestVideos[videoIndex];
    if (card && Boolean(video) === card.classList.contains("short-empty-card")) {
      removeShortFrame(card);
      card.remove();
      card = null;
    }
    if (!card) {
      card = video
        ? createShortCard(video, video.channel_title || "YouTube", null, videoIndex)
        : createShortEmptyCard(
          "다음 쇼츠를 불러오지 못했어요",
          state.youtube.guestError,
          { label: "다시 불러오기", onClick: loadGuestShorts },
          videoIndex,
        );
      insertShortCardInOrder(card, videoIndex);
    }
    positionShortCard(card, videoIndex, height);
  }
  state.youtube.virtualStart = range.start;
  state.youtube.virtualEnd = range.end;
  state.youtube.virtualHeight = height;
  // Loading or removing a cross-origin player must not become the browser's
  // scroll anchor and advance the logical feed without user input.
  if (Math.abs(shortsView.scrollTop - preservedScrollTop) > 1) {
    shortsView.scrollTop = preservedScrollTop;
  }
  syncActiveShortAudio();
}

function updateShortsFromScroll() {
  const itemCount = shortLogicalItemCount();
  if (!itemCount) return;
  const height = shortViewportHeight();
  const activeIndex = Math.max(0, Math.min(itemCount - 1, Math.round(shortsView.scrollTop / height)));
  if (activeIndex !== state.youtube.activeIndex) {
    state.youtube.activeIndex = activeIndex;
    renderShortWindow();
  } else {
    syncActiveShortAudio();
  }
  maybeLoadMoreGuestShorts();
  scheduleShortScrollSnap();
}

function scheduleShortScrollSnap() {
  window.clearTimeout(state.shortSnapTimer);
  state.shortSnapTimer = window.setTimeout(() => {
    state.shortSnapTimer = null;
    if (state.activeList !== "shorts" || document.hidden) return;
    const target = state.youtube.activeIndex * shortViewportHeight();
    if (Math.abs(shortsView.scrollTop - target) > 1) shortsView.scrollTop = target;
  }, 120);
}

function resizeShortWindow() {
  if (state.activeList !== "shorts" || !shortLogicalItemCount()) return;
  const activeIndex = state.youtube.activeIndex;
  state.youtube.virtualHeight = 0;
  renderShortWindow(true);
  shortsView.scrollTop = activeIndex * shortViewportHeight();
}

function renderShorts() {
  if (state.youtube.renderedFeedVersion !== state.youtube.feedVersion) {
    releaseAllShortFrames();
    shortsFeed.replaceChildren();
    state.youtube.renderedFeedVersion = state.youtube.feedVersion;
    state.youtube.activeIndex = 0;
    state.youtube.virtualStart = -1;
    state.youtube.virtualEnd = -1;
    state.youtube.virtualHeight = 0;
  }

  if (!state.youtube.guestVideos.length) {
    shortsFeed.style.height = `${shortViewportHeight()}px`;
    shortsFeed.replaceChildren(createShortEmptyCard(
      state.youtube.guestError ? "쇼츠를 불러오지 못했어요" : "YouTube Shorts",
      state.youtube.guestError || "한국 쇼츠를 불러오는 중이에요.",
      state.youtube.guestLoading
        ? null
        : { label: "다시 불러오기", onClick: loadGuestShorts },
    ));
    positionShortCard(shortsFeed.firstElementChild, 0, shortViewportHeight());
    return;
  }

  renderShortWindow();
  renderShortShareBar();
}

async function loadGuestShorts(refresh = false) {
  if (state.youtube.guestLoading) return;
  const feedVersion = state.youtube.feedVersion;
  state.youtube.guestLoading = true;
  state.youtube.guestError = "";
  renderShorts();

  try {
    const requestQuery = state.youtube.guestCursor
      ? `?cursor=${encodeURIComponent(state.youtube.guestCursor)}`
      : refresh
        ? "?refresh=1"
        : "";
    const payload = await requestAction("shorts.load", `/youtube/shorts${requestQuery}`);
    if (feedVersion !== state.youtube.feedVersion) return;
    const existingIds = new Set(state.youtube.guestVideos.map((video) => video.id));
    const batchIds = new Set();
    const newVideos = (payload.items || []).filter((video) => {
      if (!video?.id || batchIds.has(video.id)) return false;
      batchIds.add(video.id);
      return payload.cycled || !existingIds.has(video.id);
    });
    const remainingCapacity = MAX_SHORTS_FEED_ITEMS - state.youtube.guestVideos.length;
    state.youtube.guestVideos.push(...newVideos.slice(0, Math.max(0, remainingCapacity)));
    state.youtube.guestCursor = remainingCapacity > newVideos.length ? (payload.next_cursor || "") : "";
    const retryAfter = Number(payload.retry_after || 0);
    if (!newVideos.length && (state.youtube.guestCursor || retryAfter > 0)) {
      window.setTimeout(() => {
        if (state.activeList === "shorts" && !state.youtube.guestLoading) {
          loadGuestShorts(!state.youtube.guestCursor);
        }
      }, retryAfter > 0 ? retryAfter * 1000 : 500);
    }
  } catch (error) {
    if (feedVersion !== state.youtube.feedVersion) return;
    state.youtube.guestError = error.message;
  } finally {
    if (feedVersion !== state.youtube.feedVersion) return;
    state.youtube.guestLoading = false;
    renderShorts();
  }
}

function maybeLoadMoreGuestShorts() {
  if (state.youtube.guestLoading || state.youtube.guestVideos.length >= MAX_SHORTS_FEED_ITEMS) return;
  const remaining = shortsView.scrollHeight - shortsView.scrollTop - shortsView.clientHeight;
  if (remaining < shortsView.clientHeight * 1.5) {
    loadGuestShorts(!state.youtube.guestCursor);
  }
}

async function loadYouTubeShorts() {
  if (!state.youtube.accessToken) return;
  state.youtube.message = "좋아요 기반 추천 쇼츠를 찾는 중이에요.";
  renderShorts();

  try {
    const likedPayload = await requestAction("shorts.load-liked", "https://www.googleapis.com/youtube/v3/videos?part=snippet&myRating=like&maxResults=1", {
      headers: { Authorization: `Bearer ${state.youtube.accessToken}` },
    });

    const seed = likedPayload.items?.[0];
    if (!seed?.id) {
      state.youtube.videos = [];
      state.youtube.seedTitle = "";
      state.youtube.message = "좋아요한 영상을 찾지 못했어요.";
      renderShorts();
      return;
    }

    const relatedUrl = new URL("https://www.googleapis.com/youtube/v3/search");
    relatedUrl.search = new URLSearchParams({
      part: "snippet",
      type: "video",
      videoDuration: "short",
      relatedToVideoId: seed.id,
      maxResults: "25",
    });
    const relatedPayload = await requestAction("shorts.load-related", relatedUrl, {
      headers: { Authorization: `Bearer ${state.youtube.accessToken}` },
    });

    const videoIds = (relatedPayload.items || []).map((item) => item.id?.videoId).filter(Boolean);
    if (!videoIds.length) {
      state.youtube.videos = [];
      state.youtube.seedTitle = seed.snippet?.title || "";
      state.youtube.message = "유사한 영상을 찾지 못했어요.";
      renderShorts();
      return;
    }

    const videosUrl = new URL("https://www.googleapis.com/youtube/v3/videos");
    videosUrl.search = new URLSearchParams({
      part: "snippet,contentDetails",
      id: videoIds.join(","),
    });
    const videosPayload = await requestAction("shorts.load-details", videosUrl, {
      headers: { Authorization: `Bearer ${state.youtube.accessToken}` },
    });

    state.youtube.videos = (videosPayload.items || []).filter((video) => youtubeDurationSeconds(video.contentDetails?.duration) > 0 && youtubeDurationSeconds(video.contentDetails?.duration) <= 60);
    state.youtube.seedTitle = seed.snippet?.title || "";
    state.youtube.activeIndex = 0;
    state.youtube.message = "";
    renderShorts();
  } catch (error) {
    state.youtube.message = error.message;
    renderShorts();
    setAppStatus(error.message, "error");
  }
}

async function connectYouTube() {
  if (!state.providers.google?.enabled) {
    state.youtube.message = "Google 로그인을 먼저 연결해 주세요.";
    renderShorts();
    return;
  }

  try {
    await loadGoogleIdentityLibrary();
    if (!window.google?.accounts?.oauth2) throw new Error("YouTube 권한 창을 열지 못했어요.");

    state.youtube.tokenClient = window.google.accounts.oauth2.initTokenClient({
      client_id: state.providers.google.client_id,
      scope: "https://www.googleapis.com/auth/youtube.readonly",
      callback: async (response) => {
        if (response.error || !response.access_token) {
          state.youtube.message = "YouTube 읽기 권한이 필요해요.";
          renderShorts();
          return;
        }
        state.youtube.accessToken = response.access_token;
        await loadYouTubeShorts();
      },
    });
    state.youtube.tokenClient.requestAccessToken({ prompt: state.youtube.accessToken ? "" : "consent" });
  } catch (error) {
    state.youtube.message = error.message;
    renderShorts();
    setAppStatus(error.message, "error");
  }
}
