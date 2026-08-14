"use strict";

// Shorts feed paging, iframe lifecycle, sharing, and inline replies.
function youtubeDurationSeconds(duration) {
  const match = /^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(duration || "");
  if (!match) return 0;
  return Number(match[1] || 0) * 3600 + Number(match[2] || 0) * 60 + Number(match[3] || 0);
}

function createShortCard(video, copy, action) {
  const card = document.createElement("article");
  card.className = "short-card";
  card.style.height = `${Math.max(shortsView.clientHeight, 1)}px`;

  const frame = document.createElement("iframe");
  frame.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share";
  frame.allowFullscreen = true;
  frame.loading = "lazy";
  frame.referrerPolicy = "strict-origin-when-cross-origin";
  frame.dataset.videoId = video.id;
  frame.dataset.src = shortEmbedSource(video.id, false);
  frame.dataset.soundEnabled = "false";
  frame.title = video.title || "YouTube 쇼츠";

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

  card.append(frame, metadata);
  return card;
}

function activeShortVideo() {
  if (!state.youtube.guestVideos.length) return null;
  const cardHeight = Math.max(shortsView.clientHeight, 1);
  const index = Math.max(0, Math.min(
    state.youtube.guestVideos.length - 1,
    Math.round(shortsView.scrollTop / cardHeight),
  ));
  return state.youtube.guestVideos[index] || null;
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
  shortShareFeedback.textContent = ""; /*
  shortShareSend.textContent = "➤";
  shortShareSend.setAttribute("aria-label", "Send");
  */ shortShareSend.textContent = ">";
  shortShareSend.setAttribute("aria-label", "Send");
  if (state.shortInlineReply) {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "short-inline-reply";
    input.maxLength = 300;
    input.autocomplete = "off";
    input.placeholder = "Reply";
    input.setAttribute("aria-label", "Reply message");
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
    shortShareSend.setAttribute("aria-label", "Send reply");
    requestAnimationFrame(() => input.focus());
    return;
  }
  if (state.shortMessageNotice) {
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
    shortShareSend.setAttribute("aria-label", "Reply");
    return;
  }
  const rooms = recentChatRooms();
  if (!rooms.length) {
    state.selectedShareRoomId = "";
    shortShareSend.disabled = true;
    /*
    const empty = document.createElement("p");
    empty.className = "short-share-empty";
    empty.textContent = "대화를 시작하면 여기서 쇼츠를 보낼 수 있어요.";
    shortShareList.appendChild(empty);
    */ return;
  }
  if (!rooms.some((room) => room.id === state.selectedShareRoomId)) state.selectedShareRoomId = "";

  rooms.forEach((room) => {
    const person = document.createElement("button");
    person.type = "button";
    person.className = "short-share-person"; /*
    person.title = `${room.name}에게 현재 쇼츠 보내기`;
    */ person.title = `Share to ${room.name}`;
    person.appendChild(createRoomAvatar(room));
    const name = document.createElement("span");
    name.textContent = room.name;
    person.appendChild(name);
    person.classList.toggle("selected", state.selectedShareRoomId === room.id);
    person.addEventListener("click", () => selectShortShareRoom(room.id));
    shortShareList.appendChild(person);
  });
  shortShareSend.disabled = !state.selectedShareRoomId;
}

function selectShortShareRoom(roomId) {
  state.selectedShareRoomId = roomId;
  renderShortShareBar();
}

async function sendSelectedShort() {
  const room = state.messenger.rooms.find((candidate) => candidate.id === state.selectedShareRoomId);
  if (room) await shareShortToRoom(room);
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
    await api("/messages", { method: "POST", body: JSON.stringify({ roomId, text }) });
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

async function shareShortToRoom(room) {
  const video = activeShortVideo();
  if (!video) {
    shortShareFeedback.textContent = "공유할 쇼츠를 먼저 불러와 주세요.";
    return;
  }
  const url = `https://www.youtube.com/shorts/${video.id}`; /*
  shortShareFeedback.textContent = `${room.name}에게 보내는 중…`;
  */ const shareUrl = `https://www.youtube.com/shorts/${video.id}`;
  shortShareFeedback.textContent = `Sending to ${room.name}...`;
  try {
    await api("/messages", {
      method: "POST",
      body: JSON.stringify({ roomId: room.id, text: shareUrl }),
    });
    shortShareFeedback.textContent = `${room.name}에게 보냈어요.`;
    shortShareSend.disabled = true;
  } catch (error) {
    shortShareFeedback.textContent = error.message;
  }
}

function shortEmbedSource(videoId, soundEnabled) {
  const origin = encodeURIComponent(window.location.origin);
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?autoplay=1&mute=${soundEnabled ? "0" : "1"}&playsinline=1&rel=0&enablejsapi=1&origin=${origin}`;
}

function setShortFrameSound(frame, soundEnabled, forceLoad = false) {
  if (forceLoad && frame.dataset.src) {
    frame.src = frame.dataset.src;
    delete frame.dataset.src;
  }
  if (!frame.src) return;
  const command = soundEnabled ? "unMute" : "mute";
  const sendCommand = () => frame.contentWindow?.postMessage(JSON.stringify({
    event: "command",
    func: command,
    args: [],
  }), "https://www.youtube-nocookie.com");
  frame.dataset.soundEnabled = String(soundEnabled);
  sendCommand();
  frame.addEventListener("load", sendCommand, { once: true });
}

function syncActiveShortAudio() {
  const cards = Array.from(shortsFeed.querySelectorAll(".short-card:not(.short-empty-card)"));
  if (!cards.length) return;
  const activeIndex = Math.max(0, Math.min(cards.length - 1, Math.round(
    shortsView.scrollTop / Math.max(shortsView.clientHeight, 1),
  )));

  cards.forEach((card, index) => {
    const frame = card.querySelector("iframe");
    if (!frame) return;
    const shouldLoad = Math.abs(index - activeIndex) <= 2;
    if (shouldLoad && frame.dataset.src) {
      frame.src = frame.dataset.src;
      delete frame.dataset.src;
    } else if (!shouldLoad && frame.src) {
      frame.dataset.src = shortEmbedSource(frame.dataset.videoId, false);
      frame.removeAttribute("src");
      frame.dataset.soundEnabled = "false";
    }
    if (shouldLoad) {
      const enableSound = state.youtube.soundEnabled && index === activeIndex;
      if (frame.dataset.soundEnabled !== String(enableSound)) setShortFrameSound(frame, enableSound);
    }
  });
  shortsSoundToggle.textContent = state.youtube.soundEnabled ? "🔊" : "🔇";
  shortsSoundToggle.setAttribute("aria-label", state.youtube.soundEnabled ? "쇼츠 소리 끄기" : "쇼츠 소리 켜기");
}

function loadVisibleShortFrames() {
  syncActiveShortAudio();
}

function createShortEmptyCard(titleText, copyText, action) {
  const card = document.createElement("article");
  card.className = "short-card short-empty-card";
  card.style.height = `${Math.max(shortsView.clientHeight, 1)}px`;
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

function renderShorts() {
  if (state.youtube.renderedFeedVersion !== state.youtube.feedVersion) {
    shortsFeed.replaceChildren();
    state.youtube.renderedFeedVersion = state.youtube.feedVersion;
    state.youtube.renderedGuestVideoCount = 0;
  }

  if (!state.youtube.guestVideos.length) {
    shortsFeed.replaceChildren(createShortEmptyCard(
      state.youtube.guestError ? "쇼츠를 불러오지 못했어요" : "YouTube Shorts",
      state.youtube.guestError || "한국 쇼츠를 불러오는 중이에요.",
      state.youtube.guestLoading
        ? null
        : { label: "다시 불러오기", onClick: loadGuestShorts },
    ));
    state.youtube.renderedGuestVideoCount = 0;
    return;
  }

  if (state.youtube.renderedGuestVideoCount === 0) {
    shortsFeed.replaceChildren();
  }
  shortsFeed.querySelector(".short-load-error")?.remove();
  state.youtube.guestVideos.slice(state.youtube.renderedGuestVideoCount).forEach((video) => {
    shortsFeed.appendChild(createShortCard(
      video,
      video.channel_title || "YouTube",
    ));
  });
  state.youtube.renderedGuestVideoCount = state.youtube.guestVideos.length;
  if (state.youtube.guestError) {
    const errorCard = createShortEmptyCard(
      "다음 쇼츠를 불러오지 못했어요",
      state.youtube.guestError,
      { label: "다시 불러오기", onClick: loadGuestShorts },
    );
    errorCard.classList.add("short-load-error");
    shortsFeed.appendChild(errorCard);
  }
  renderShortShareBar();
  loadVisibleShortFrames();
  syncActiveShortAudio();
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
    const payload = await api(`/youtube/shorts${requestQuery}`);
    if (feedVersion !== state.youtube.feedVersion) return;
    const existingIds = new Set(state.youtube.guestVideos.map((video) => video.id));
    const newVideos = (payload.items || []).filter((video) => video?.id && !existingIds.has(video.id));
    const remainingCapacity = MAX_SHORTS_FEED_ITEMS - state.youtube.guestVideos.length;
    state.youtube.guestVideos.push(...newVideos.slice(0, Math.max(0, remainingCapacity)));
    state.youtube.guestCursor = remainingCapacity > newVideos.length ? (payload.next_cursor || "") : "";
    const retryAfter = Number(payload.retry_after || 0);
    if (!newVideos.length && (state.youtube.guestCursor || retryAfter > 0)) {
      window.setTimeout(() => {
        if (state.activeList === "shorts" && !state.youtube.guestLoading) {
          loadGuestShorts(!state.youtube.guestCursor);
        }
      }, retryAfter > 0 ? retryAfter * 1000 : 120);
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
    const likedResponse = await fetch("https://www.googleapis.com/youtube/v3/videos?part=snippet&myRating=like&maxResults=1", {
      headers: { Authorization: `Bearer ${state.youtube.accessToken}` },
    });
    const likedPayload = await likedResponse.json();
    if (!likedResponse.ok) throw new Error(likedPayload?.error?.message || "좋아요한 영상을 불러오지 못했어요.");

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
    const relatedResponse = await fetch(relatedUrl, {
      headers: { Authorization: `Bearer ${state.youtube.accessToken}` },
    });
    const relatedPayload = await relatedResponse.json();
    if (!relatedResponse.ok) throw new Error(relatedPayload?.error?.message || "유사한 영상을 불러오지 못했어요.");

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
    const videosResponse = await fetch(videosUrl, {
      headers: { Authorization: `Bearer ${state.youtube.accessToken}` },
    });
    const videosPayload = await videosResponse.json();
    if (!videosResponse.ok) throw new Error(videosPayload?.error?.message || "추천 영상을 불러오지 못했어요.");

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
