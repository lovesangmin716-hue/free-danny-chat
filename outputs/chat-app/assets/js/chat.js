"use strict";

// Message state, incremental rendering, pagination, retries, and sending.
const MESSAGE_TIME_CLUSTER_MS = 5 * 60 * 1000;
const MESSAGE_READ_SWIPE_THRESHOLD = 34;
const MESSAGE_ERASE_TURN_DISTANCE = 18;
const MESSAGE_ERASE_REQUIRED_TURNS = 4;
const MESSAGE_ERASE_REQUIRED_TRAVEL = 150;
const MESSAGE_ERASE_MAX_DURATION_MS = 3000;
let messageReadSwipe = null;
let suppressMessageClick = false;

function shouldShowMessageTime(message, nextMessage) {
  if (!nextMessage || nextMessage.username !== message.username) return true;
  const timestamp = Date.parse(message.timestamp);
  const nextTimestamp = Date.parse(nextMessage.timestamp);
  if (!Number.isFinite(timestamp) || !Number.isFinite(nextTimestamp) || nextTimestamp < timestamp) return true;
  return nextTimestamp - timestamp > MESSAGE_TIME_CLUSTER_MS;
}

function messageSenderDisplayName(room, message) {
  if (message.username === state.messenger.user?.username) {
    return state.messenger.user?.display_name || state.messenger.user?.username || message.username;
  }
  if (room?.peer?.username === message.username) {
    return room.peer.display_name || room.peer.username;
  }
  return roomParticipantDisplayName(room, message.username);
}

function roomReadParticipants(room) {
  const participants = [
    state.messenger.user,
    room?.peer,
    ...(room?.participants || []),
  ];
  const unique = new Map();
  for (const participant of participants) {
    if (participant?.username && !unique.has(participant.username)) {
      unique.set(participant.username, {
        id: participant.id || "",
        username: participant.username,
        display_name: participant.display_name || participant.username,
      });
    }
  }
  return [...unique.values()];
}

function addMessageReader(message, username, room = currentRoom()) {
  if (!message || !username || username === message.username) return message;
  const existingReaders = Array.isArray(message.read_by) ? message.read_by : [];
  if (existingReaders.some((reader) => reader.username === username)) return message;
  const reader = roomReadParticipants(room).find((candidate) => candidate.username === username) || {
    id: "",
    username,
    display_name: username,
  };
  const readBy = [...existingReaders, reader];
  const eligibleReaders = roomReadParticipants(room).filter(
    (candidate) => candidate.username !== message.username,
  );
  const unreadSource = Array.isArray(message.unread_by) ? message.unread_by : eligibleReaders;
  const unreadBy = unreadSource.filter((candidate) => candidate.username !== username);
  return {
    ...message,
    read_by: readBy,
    unread_by: unreadBy,
    read: message.username === state.messenger.user?.username ? unreadBy.length === 0 : Boolean(message.read),
  };
}

function applyMessageReaderToCurrentMessages(username, transactionName = "messages.reader") {
  const changedMessages = [];
  appStore.transact(transactionName, () => {
    // Read positions are monotonic. Walk only the unread tail and stop at the
    // first eligible message that already contains this reader.
    for (let index = state.messages.length - 1; index >= 0; index -= 1) {
      const message = state.messages[index];
      if (message.username === username) continue;
      if ((message.read_by || []).some((reader) => reader.username === username)) break;
      const readMessage = addMessageReader(message, username);
      if (readMessage === message) continue;
      state.messages[index] = readMessage;
      changedMessages.push([message.id, readMessage]);
    }
    if (changedMessages.length) state.messageRevision += 1;
  });
  for (const [messageId, message] of changedMessages) replaceChatMessageNode(messageId, message);
  if (changedMessages.length && state.renderedMessageRoomId === state.selectedRoomId) {
    state.renderedMessageRevision = state.messageRevision;
  }
}

function nextOwnMessageIndex(messageIndex) {
  const username = state.messenger.user?.username;
  for (let index = messageIndex + 1; index < state.messages.length; index += 1) {
    if (state.messages[index].username === username) return index;
  }
  return -1;
}

function previousOwnMessageIndex(messageIndex) {
  const username = state.messenger.user?.username;
  for (let index = messageIndex - 1; index >= 0; index -= 1) {
    if (state.messages[index].username === username) return index;
  }
  return -1;
}

function shouldShowMessageReadReceipt(message, messageIndex) {
  if (message.username !== state.messenger.user?.username) return false;
  if (message.pending || message.failed) return true;
  const nextIndex = nextOwnMessageIndex(messageIndex);
  if (nextIndex < 0) return true;
  const nextReaders = new Set((state.messages[nextIndex].read_by || []).map((reader) => reader.username));
  return (message.read_by || []).some((reader) => !nextReaders.has(reader.username));
}

function messageReadReceiptLabel(message, room = currentRoom()) {
  if (message.pending) return "보내는 중";
  if (message.failed) return "전송 실패";
  const readerCount = Array.isArray(message.read_by) ? message.read_by.length : 0;
  return room?.kind === "group" ? `${readerCount}명 읽음` : (readerCount ? "읽음" : "안 읽음");
}

function syncMessageMetaEmpty(row) {
  const meta = row.querySelector(".message-meta");
  if (!meta) return;
  meta.classList.toggle("message-meta-empty", ![...meta.children].some(
    (child) => !child.classList.contains("hidden"),
  ));
}

function setMessageGroupMetaVisibility(row, message, nextMessage) {
  const showGroupMeta = shouldShowMessageTime(message, nextMessage);
  row.querySelector("time")?.classList.toggle("hidden", !showGroupMeta);
  row.querySelector(".message-sender")?.classList.toggle("hidden", !showGroupMeta);
  syncMessageMetaEmpty(row);
}

function syncMessageReadReceiptVisibility(messageIndex) {
  if (!Number.isInteger(messageIndex) || messageIndex < 0 || messageIndex >= state.messages.length) return;
  const message = state.messages[messageIndex];
  const row = state.messageNodes.get(message.id);
  const receipt = row?.querySelector(".message-read, .message-unread");
  if (!row || !receipt) return;
  receipt.textContent = messageReadReceiptLabel(message);
  const hasReaders = Array.isArray(message.read_by) && message.read_by.length > 0;
  receipt.className = message.failed || !hasReaders ? "message-unread" : "message-read";
  receipt.classList.toggle("hidden", !shouldShowMessageReadReceipt(message, messageIndex));
  syncMessageMetaEmpty(row);
}

function createChatMessageRow(message, nextMessage = null, messageIndex = -1) {
  const mine = message.username === state.messenger.user?.username;
  const row = document.createElement("article");
  row.className = `message-row ${mine ? "mine" : "theirs"}${message.pending ? " pending" : ""}${message.failed ? " failed" : ""}`;
  row.dataset.messageId = message.id;
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  const room = currentRoom();
  if (message.attachment?.url) {
    const attachment = message.attachment;
    const attachmentLink = document.createElement("a");
    attachmentLink.className = "message-attachment";
    attachmentLink.href = attachment.url;
    attachmentLink.target = "_blank";
    attachmentLink.rel = "noopener";
    if (attachment.type?.startsWith("image/")) {
      const image = document.createElement("img");
      image.className = "message-image";
      image.src = attachment.url;
      image.alt = attachment.name || "Attached photo";
      image.loading = "lazy";
      image.addEventListener("load", () => {
        if (!row.isConnected) return;
        state.messageHeights.delete(message.id);
        requestAnimationFrame(() => measureRenderedChatMessages());
      }, { once: true });
      attachmentLink.appendChild(image);
    } else {
      attachmentLink.classList.add("message-file");
      attachmentLink.textContent = `PDF · ${attachment.name || "attachment.pdf"}`;
    }
    bubble.appendChild(attachmentLink);
  }
  if (message.text) {
    const messageText = document.createElement("span");
    messageText.textContent = message.text;
    bubble.appendChild(messageText);
  }
  const meta = document.createElement("div");
  meta.className = "message-meta";
  if (mine) {
    const read = document.createElement("span");
    const hasReaders = Array.isArray(message.read_by) && message.read_by.length > 0;
    read.className = message.failed || !hasReaders ? "message-unread" : "message-read";
    read.textContent = messageReadReceiptLabel(message, room);
    read.classList.toggle("hidden", !shouldShowMessageReadReceipt(message, messageIndex));
    meta.appendChild(read);
  }
  if (!mine) {
    const sender = document.createElement("strong");
    sender.className = "message-sender";
    sender.textContent = messageSenderDisplayName(room, message);
    meta.appendChild(sender);
  }
  const time = document.createElement("time");
  time.textContent = formatTime(message.timestamp);
  meta.appendChild(time);
  row.append(bubble, meta);
  setMessageGroupMetaVisibility(row, message, nextMessage);
  return row;
}

function syncMessageTimeVisibility(messageIndex) {
  if (!Number.isInteger(messageIndex) || messageIndex < 0 || messageIndex >= state.messages.length) return;
  const message = state.messages[messageIndex];
  const row = state.messageNodes.get(message.id);
  if (!row) return;
  setMessageGroupMetaVisibility(row, message, state.messages[messageIndex + 1]);
}

function closeMessageReadMenu() {
  messageReadMenu.classList.add("hidden");
  messageReadMenu.setAttribute("aria-hidden", "true");
}

function openMessageReadMenu(message, row) {
  const unreadNames = (message.unread_by || [])
    .map((reader) => reader.display_name || reader.username)
    .filter(Boolean);
  messageReadMenuTitle.textContent = `안 읽은 사람 ${unreadNames.length}명`;
  messageReadMenuCopy.textContent = unreadNames.length
    ? unreadNames.join(", ")
    : "모두 읽었어요.";
  messageReadMenu.classList.remove("hidden");
  messageReadMenu.setAttribute("aria-hidden", "false");
  const width = messageReadMenu.offsetWidth;
  const height = messageReadMenu.offsetHeight;
  const rect = row.getBoundingClientRect();
  const preferredLeft = row.classList.contains("mine") ? rect.left - width - 10 : rect.right + 10;
  messageReadMenu.style.left = `${Math.max(8, Math.min(preferredLeft, window.innerWidth - width - 8))}px`;
  messageReadMenu.style.top = `${Math.max(8, Math.min(rect.top + ((rect.height - height) / 2), window.innerHeight - height - 8))}px`;
}

function beginMessageReadSwipe(event) {
  if (event.button !== undefined && event.button !== 0) return;
  const row = event.target.closest?.(".message-row");
  if (!row || !currentRoom()) return;
  const message = state.messages[state.messageIndexes.get(row.dataset.messageId)];
  if (!message || message.pending || message.failed) return;
  closeMessageReadMenu();
  messageReadSwipe = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    message,
    row,
    revealed: false,
    canErase: message.username === state.messenger.user?.username,
    startedAt: performance.now(),
    lastX: event.clientX,
    directionAnchorX: event.clientX,
    direction: 0,
    turns: 0,
    travel: 0,
    erasing: false,
    deleting: false,
  };
  row.setPointerCapture?.(event.pointerId);
}

function updateMessageReadSwipe(event) {
  const swipe = messageReadSwipe;
  if (!swipe || swipe.pointerId !== event.pointerId || swipe.deleting) return;
  const deltaX = event.clientX - swipe.startX;
  const deltaY = event.clientY - swipe.startY;
  const horizontal = Math.abs(deltaX) > Math.abs(deltaY) * 1.15;
  if (!horizontal) return;
  event.preventDefault();
  if (!swipe.revealed && deltaX <= -MESSAGE_READ_SWIPE_THRESHOLD) {
    swipe.revealed = true;
    swipe.row.classList.add("showing-readers");
    openMessageReadMenu(swipe.message, swipe.row);
  }

  if (!swipe.canErase) return;
  swipe.travel += Math.abs(event.clientX - swipe.lastX);
  swipe.lastX = event.clientX;
  const directionDelta = event.clientX - swipe.directionAnchorX;
  if (Math.abs(directionDelta) >= MESSAGE_ERASE_TURN_DISTANCE) {
    const nextDirection = Math.sign(directionDelta);
    if (swipe.direction && nextDirection !== swipe.direction) swipe.turns += 1;
    swipe.direction = nextDirection;
    swipe.directionAnchorX = event.clientX;
  }
  if (swipe.turns > 0) {
    swipe.erasing = true;
    swipe.row.classList.add("erasing");
    swipe.row.classList.remove("showing-readers");
    closeMessageReadMenu();
    const turnProgress = swipe.turns / MESSAGE_ERASE_REQUIRED_TURNS;
    const travelProgress = swipe.travel / MESSAGE_ERASE_REQUIRED_TRAVEL;
    const eraseProgress = Math.min(1, Math.min(turnProgress, travelProgress));
    swipe.row.style.setProperty("--erase-progress", String(eraseProgress));
    swipe.row.style.setProperty("--erase-opacity", String(1 - (eraseProgress * 0.62)));
    swipe.row.style.setProperty("--erase-offset", `${(eraseProgress - 0.5) * 8}px`);
  }
  if (
    swipe.turns >= MESSAGE_ERASE_REQUIRED_TURNS
    && swipe.travel >= MESSAGE_ERASE_REQUIRED_TRAVEL
    && performance.now() - swipe.startedAt <= MESSAGE_ERASE_MAX_DURATION_MS
  ) {
    swipe.deleting = true;
    swipe.row.classList.add("erase-committing");
    void deleteChatMessage(swipe.message, swipe.row);
  }
}

function finishMessageReadSwipe(event) {
  const swipe = messageReadSwipe;
  if (!swipe || (event?.pointerId !== undefined && swipe.pointerId !== event.pointerId)) return;
  if (swipe.revealed || swipe.erasing) {
    suppressMessageClick = true;
    window.setTimeout(() => { suppressMessageClick = false; }, 0);
  }
  messageReadSwipe = null;
  swipe.row.classList.remove("showing-readers");
  if (!swipe.deleting) {
    swipe.row.classList.remove("erasing");
    swipe.row.style.removeProperty("--erase-progress");
    swipe.row.style.removeProperty("--erase-opacity");
    swipe.row.style.removeProperty("--erase-offset");
  }
  if (swipe.row.hasPointerCapture?.(swipe.pointerId)) swipe.row.releasePointerCapture(swipe.pointerId);
  closeMessageReadMenu();
}

function suppressMessageReadContextMenu(event) {
  if (event.target.closest?.(".message-row")) event.preventDefault();
}

function suppressClickAfterMessageSwipe(event) {
  if (!suppressMessageClick) return;
  event.preventDefault();
  event.stopPropagation();
}

function rebuildMessageIndexes() {
  state.messageIndexes.clear();
  for (let index = 0; index < state.messages.length; index += 1) {
    state.messageIndexes.set(state.messages[index].id, index);
  }
}

function setChatMessages(messages) {
  state.messages = messages.length > CHAT_MESSAGE_MEMORY_LIMIT
    ? messages.slice(-CHAT_MESSAGE_MEMORY_LIMIT)
    : messages;
  rebuildMessageIndexes();
  const messageIds = new Set(state.messages.map((message) => message.id));
  for (const messageId of state.messageHeights.keys()) {
    if (!messageIds.has(messageId)) state.messageHeights.delete(messageId);
  }
  state.renderedMessageStart = -1;
  state.renderedMessageEnd = -1;
  state.messageRevision += 1;
}

function trimChatMessageHistory() {
  const overflow = state.messages.length - CHAT_MESSAGE_MEMORY_LIMIT;
  if (overflow <= 0) return 0;
  const removedMessages = state.messages.splice(0, overflow);
  for (const message of removedMessages) {
    state.messageHeights.delete(message.id);
    state.messageNodes.delete(message.id);
  }
  rebuildMessageIndexes();
  // The first retained message becomes the cursor boundary, so dropped rows
  // can still be fetched again if the user scrolls upward.
  state.messagesNextCursor = state.messages[0]?.id || state.messagesNextCursor;
  state.renderedMessageStart = -1;
  state.renderedMessageEnd = -1;
  return overflow;
}

function appendChatMessageState(message) {
  if (!message?.id || state.messageIndexes.has(message.id)) return false;
  state.messageIndexes.set(message.id, state.messages.length);
  state.messages.push(message);
  trimChatMessageHistory();
  state.messageRevision += 1;
  return true;
}

function replaceChatMessageState(messageId, message) {
  const index = state.messageIndexes.get(messageId);
  if (index === undefined) return false;
  state.messages[index] = message;
  state.messageIndexes.delete(messageId);
  state.messageIndexes.set(message.id, index);
  state.messageRevision += 1;
  return true;
}

function removeChatMessageState(messageId) {
  const index = state.messageIndexes.get(messageId);
  if (index === undefined) return false;
  state.messages.splice(index, 1);
  state.messageNodes.delete(messageId);
  rebuildMessageIndexes();
  state.messageRevision += 1;
  return true;
}

async function deleteChatMessage(message, row) {
  const roomId = state.selectedRoomId;
  try {
    const payload = await requestAction("messages.delete", "/messages/delete", {
      method: "POST",
      body: JSON.stringify({ roomId, messageId: message.id }),
    });
    if (payload.room) upsertRoomAfterMessageDeletion(payload.room);
    if (state.selectedRoomId === roomId && removeChatMessageState(message.id)) {
      renderAllChatMessages();
    }
    if (state.activeList !== "shorts") renderChats();
    renderShortShareBar();
    setAppStatus("메시지를 지웠어요.", "success");
  } catch (error) {
    row.classList.remove("erasing", "erase-committing");
    row.style.removeProperty("--erase-progress");
    row.style.removeProperty("--erase-opacity");
    row.style.removeProperty("--erase-offset");
    setAppStatus(error.message, "error");
  }
}

function renderAllChatMessages({ scrollToBottom = false, preserveScrollHeight = 0 } = {}) {
  if (state.chatVirtualFrame !== null) cancelAnimationFrame(state.chatVirtualFrame);
  state.chatVirtualFrame = null;
  const previousScrollTop = chatMessageList.scrollTop;
  const previousScrollHeight = preserveScrollHeight || chatMessageList.scrollHeight;
  const wasNearBottom = chatMessageList.scrollHeight - chatMessageList.clientHeight - previousScrollTop < 80;
  const renderId = state.chatVirtualRenderId + 1;
  state.chatVirtualRenderId = renderId;
  state.chatVirtualAdjusting = true;
  state.messageNodes.clear();
  chatMessageList.replaceChildren();
  if (!state.messages.length) {
    state.renderedMessageStart = 0;
    state.renderedMessageEnd = 0;
    const empty = document.createElement("p");
    empty.className = "chat-empty";
    empty.textContent = state.messagesInitialLoading ? "채팅을 불러오는 중…" : "첫 메시지를 보내 보세요.";
    chatMessageList.appendChild(empty);
  } else {
    const range = chatVirtualRange({ scrollToBottom });
    state.renderedMessageStart = range.start;
    state.renderedMessageEnd = range.end;
    const fragment = document.createDocumentFragment();
    fragment.appendChild(createChatVirtualSpacer("chat-virtual-spacer-top", range.offsets[range.start] || 0));
    for (let index = range.start; index < range.end; index += 1) {
      const message = state.messages[index];
      const row = createChatMessageRow(message, state.messages[index + 1], index);
      state.messageNodes.set(message.id, row);
      fragment.appendChild(row);
    }
    fragment.appendChild(createChatVirtualSpacer(
      "chat-virtual-spacer-bottom",
      range.totalHeight - (range.offsets[range.end] || range.totalHeight),
    ));
    chatMessageList.appendChild(fragment);
  }
  if (scrollToBottom) chatMessageList.scrollTop = chatMessageList.scrollHeight;
  state.renderedMessageRevision = state.messageRevision;
  state.renderedMessageRoomId = state.selectedRoomId;
  requestAnimationFrame(() => {
    if (state.chatVirtualRenderId !== renderId) return;
    if (state.renderedMessageRoomId !== state.selectedRoomId) {
      state.chatVirtualAdjusting = false;
      return;
    }
    if (preserveScrollHeight) {
      chatMessageList.scrollTop = previousScrollTop + chatMessageList.scrollHeight - previousScrollHeight;
    } else {
      chatMessageList.scrollTop = scrollToBottom || wasNearBottom
        ? chatMessageList.scrollHeight
        : previousScrollTop;
    }
    measureRenderedChatMessages();
    if (scrollToBottom || wasNearBottom) chatMessageList.scrollTop = chatMessageList.scrollHeight;
    state.chatVirtualAdjusting = false;
    if (
      state.messagesNextCursor
      && chatMessageList.scrollHeight <= chatMessageList.clientHeight + 1
    ) void loadOlderChatMessages();
  });
}

function scheduleChatVirtualRender() {
  if (
    state.chatVirtualAdjusting
    || state.chatVirtualFrame !== null
    || state.renderedMessageRoomId !== state.selectedRoomId
  ) return;
  state.chatVirtualFrame = requestAnimationFrame(() => {
    state.chatVirtualFrame = null;
    if (state.renderedMessageRoomId !== state.selectedRoomId || !state.messages.length) return;
    const range = chatVirtualRange();
    if (range.start !== state.renderedMessageStart || range.end !== state.renderedMessageEnd) {
      renderAllChatMessages();
    }
  });
}

function appendChatMessageNode(_message, scrollToBottom = false) {
  renderAllChatMessages({ scrollToBottom });
}

function replaceChatMessageNode(messageId, message) {
  const currentRow = state.messageNodes.get(messageId);
  if (!currentRow) {
    state.renderedMessageRevision = state.messageRevision;
    return;
  }
  const messageIndex = state.messageIndexes.get(message.id);
  const nextRow = createChatMessageRow(
    message,
    Number.isInteger(messageIndex) ? state.messages[messageIndex + 1] : null,
    messageIndex,
  );
  currentRow.replaceWith(nextRow);
  state.messageNodes.delete(messageId);
  state.messageNodes.set(message.id, nextRow);
  syncMessageTimeVisibility(messageIndex - 1);
  syncMessageReadReceiptVisibility(messageIndex);
  syncMessageReadReceiptVisibility(previousOwnMessageIndex(messageIndex));
  state.renderedMessageRevision = state.messageRevision;
  requestAnimationFrame(() => measureRenderedChatMessages());
}

function renderChatRoom({ scrollToBottom = false, preserveScrollHeight = 0 } = {}) {
  const room = currentRoom();
  if (!room) {
    chatRoom.classList.add("hidden");
    syncAppStatusForActiveTab();
    openRoomSettingsButton.classList.add("hidden");
    roomSettingsSheet.classList.add("hidden");
    return;
  }
  const presence = room.peer?.presence;
  const isGroupRoom = room.kind === "group";
  const isInThisRoom = Boolean(presence?.online && presence.active_room_ids?.includes(room.id));
  chatRoom.classList.remove("hidden");
  syncAppStatusForActiveTab();
  openRoomSettingsButton.classList.toggle("hidden", !isGroupRoom);
  renderChatAttachmentTray();
  renderChatAttachmentPreview();
  const draft = state.chatDrafts[room.id] || "";
  if (chatMessageInput.value !== draft) chatMessageInput.value = draft;
  chatRoomAvatar.replaceChildren(createRoomAvatar(room));
  chatRoomName.textContent = room.name; /*
  chatRoomPresence.textContent = isInThisRoom ? "대화방에 접속 중" : (presence?.online ? "활동 중" : "");

  */ chatRoomPresence.textContent = isGroupRoom
    ? `${room.participant_count || room.participants?.length || 0}명`
    : (isInThisRoom ? "in chat" : (presence?.online ? "online" : ""));
  if (
    state.renderedMessageRoomId !== room.id
    || state.renderedMessageRevision !== state.messageRevision
  ) {
    renderAllChatMessages({ scrollToBottom, preserveScrollHeight });
  } else if (scrollToBottom) {
    chatMessageList.scrollTop = chatMessageList.scrollHeight;
  }
}

async function updatePresence() {
  if (!state.session?.user) return;
  const emoji = state.selectedStatusEmoji || normalizeStatusEmoji(state.messenger.user?.status_message);
  try {
    await requestAction("presence.update", "/presence", {
      method: "POST",
      body: JSON.stringify({ activeRoomId: document.hidden ? "" : state.selectedRoomId, emoji }),
    });
  } catch (_) {
  }
}

function scheduleRoomRead(roomId) {
  if (!roomId) return;
  window.clearTimeout(state.roomReadTimers.get(roomId));
  const timer = window.setTimeout(() => {
    state.roomReadTimers.delete(roomId);
    void requestAction("rooms.mark-read", "/rooms/read", {
      method: "POST",
      body: JSON.stringify({ roomId }),
    }).then(() => {
      if (state.selectedRoomId === roomId) {
        applyMessageReaderToCurrentMessages(state.messenger.user?.username, "messages.mark-read");
      }
    }).catch(() => {});
  }, 120);
  state.roomReadTimers.set(roomId, timer);
}

async function loadChatMessages({ markRead = true, scrollToBottom = false, aroundMessageId = "" } = {}) {
  if (!state.selectedRoomId) return;
  const roomId = state.selectedRoomId;
  state.messagesLoadController?.abort();
  const controller = new AbortController();
  const loadEpoch = state.messagesLoadEpoch + 1;
  state.messagesLoadEpoch = loadEpoch;
  state.messagesLoadController = controller;
  state.messagesInitialLoading = true;
  renderChatRoom();
  try {
    const aroundQuery = aroundMessageId ? `&around=${encodeURIComponent(aroundMessageId)}` : "";
    const payload = await requestAction(
      "messages.load",
      `/messages?room_id=${encodeURIComponent(roomId)}&limit=${CHAT_MESSAGE_PAGE_SIZE}${aroundQuery}`,
      { signal: controller.signal },
    );
    if (state.selectedRoomId !== roomId || state.messagesLoadEpoch !== loadEpoch) return;
    const messages = Array.isArray(payload) ? payload : (payload.items || []);
    const serverClientMessageIds = new Set();
    for (const message of messages) {
      if (message.client_message_id) serverClientMessageIds.add(message.client_message_id);
    }
    for (const pendingMessage of state.messages) {
      if (
        pendingMessage.pending
        && !serverClientMessageIds.has(pendingMessage.client_message_id)
      ) messages.push(pendingMessage);
    }
    setChatMessages(messages);
    state.messagesNextCursor = Array.isArray(payload) ? "" : (payload.next_cursor || "");
    renderChatRoom({ scrollToBottom });
    if (aroundMessageId) {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const target = state.messageNodes.get(aroundMessageId);
        if (!target) return;
        target.scrollIntoView({ block: "center", behavior: "smooth" });
        target.classList.add("search-target");
        window.setTimeout(() => target.classList.remove("search-target"), 1900);
      }));
    }
    if (markRead && state.selectedRoomId === roomId) {
      scheduleRoomRead(roomId);
    }
  } catch (error) {
    if (error?.name === "AbortError") return;
    setAppStatus(error.message, "error");
  } finally {
    if (state.messagesLoadEpoch === loadEpoch) {
      state.messagesLoadController = null;
      state.messagesInitialLoading = false;
      if (state.selectedRoomId === roomId) {
        if (!state.messages.length) state.renderedMessageRoomId = "";
        renderChatRoom();
      }
    }
  }
}

function unloadChatMessages() {
  state.messagesLoadEpoch += 1;
  state.messagesLoadController?.abort();
  state.messagesOlderLoadController?.abort();
  if (state.chatVirtualFrame !== null) cancelAnimationFrame(state.chatVirtualFrame);
  state.chatVirtualRenderId += 1;
  state.chatVirtualFrame = null;
  state.chatVirtualAdjusting = false;
  state.messagesLoadController = null;
  state.messagesOlderLoadController = null;
  state.messagesInitialLoading = false;
  state.messagesLoadingOlder = false;
  setChatMessages([]);
  state.messagesNextCursor = "";
  state.messageNodes.clear();
  state.messageHeights.clear();
  state.renderedMessageStart = -1;
  state.renderedMessageEnd = -1;
  chatMessageList.replaceChildren();
}

async function loadRoomMembers(roomId, { reset = true } = {}) {
  const room = state.roomById.get(roomId);
  if (!room || room.kind !== "group" || state.roomMembersLoading.has(roomId)) return;
  const cursor = reset ? "" : (state.roomMemberCursors.get(roomId) || "");
  if (!reset && !cursor) return;
  state.roomMembersLoading.add(roomId);
  try {
    const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    const page = await requestAction(
      "rooms.members",
      `/rooms/${encodeURIComponent(roomId)}/members?limit=50${suffix}`,
    );
    if (state.roomById.get(roomId) !== room) return;
    const existing = reset ? [] : (room.participants || []);
    room.participants = mergeEntitiesById(existing, page.items || [], false);
    room.participant_count = Math.max(room.participant_count || 0, room.participants.length);
    state.roomMemberCursors.set(roomId, page.next_cursor || "");
    if (state.selectedRoomId === roomId) {
      state.renderedMessageRevision = -1;
      renderChatRoom();
    }
  } catch (_) {
  } finally {
    state.roomMembersLoading.delete(roomId);
  }
}

async function openChatRoom(roomId, { aroundMessageId = "", focusInput = true } = {}) {
  if (state.selectedRoomId && state.selectedRoomId !== roomId) unloadChatMessages();
  state.selectedRoomId = roomId;
  state.messagesInitialLoading = true;
  state.renderedMessageRoomId = "";
  chatMessageInput.value = state.chatDrafts[roomId] || "";
  renderChatRoom();
  await Promise.all([
    updatePresence(),
    loadRoomMembers(roomId),
    loadChatMessages({ scrollToBottom: !aroundMessageId, aroundMessageId }),
  ]);
  if (focusInput) chatMessageInput.focus({ preventScroll: true });
}

async function openChatRoomAtMessage(room, messageId) {
  if (!state.roomById.has(room.id)) upsertMessengerRoom(room);
  await openChatRoom(room.id, { aroundMessageId: messageId, focusInput: false });
}

function closeChatRoom() {
  const wasReplyingToShortNotice = state.activeList === "shorts" && state.shortMessagePaused;
  state.selectedRoomId = "";
  unloadChatMessages();
  state.renderedMessageRoomId = "";
  closeMessageReadMenu();
  clearChatAttachment();
  if (state.roomImageProcessing) {
    state.roomImageSelectionId += 1;
    state.roomImageProcessing = false;
    ColorlessImageProcessing.cancel("room-image");
  }
  chatRoom.classList.add("hidden");
  syncAppStatusForActiveTab();
  roomSettingsSheet.classList.add("hidden");
  updatePresence();
  if (wasReplyingToShortNotice) resumeShortMessageNotice();
}

function createClientMessageId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `client_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 14)}`;
}

function retryDelay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function postChatMessageWithRetry(payload) {
  const maxAttempts = 3;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      return await requestAction("messages.send", "/messages", { method: "POST", body: JSON.stringify(payload) });
    } catch (error) {
      const retryable = !error?.status || error.status === 429 || error.status >= 500;
      if (!retryable || attempt === maxAttempts - 1) throw error;
      const retryAfter = Math.min(Number(error.retryAfter || 0) * 1000, 3000);
      const backoff = 250 * (2 ** attempt) + Math.floor(Math.random() * 100);
      await retryDelay(Math.max(retryAfter, backoff));
    }
  }
  throw new Error("메시지를 전송하지 못했습니다.");
}

function sendChatMessage(event) {
  event.preventDefault();
  if (state.chatAttachmentPreparing) {
    setAppStatus("이미지 크기를 줄이는 중이에요. 잠시만 기다려 주세요.");
    return;
  }
  const roomId = state.selectedRoomId;
  const text = (chatMessageInput.value || state.chatDrafts[roomId] || "").trim();
  const attachmentFile = state.chatAttachment;
  const attachmentType = state.chatAttachmentType;
  const attachmentUpload = state.chatAttachmentUpload;
  if (!roomId || (!text && !attachmentFile)) return;

  const clientMessageId = createClientMessageId();
  const pendingId = `pending-${clientMessageId}`;
  const previewUrl = attachmentFile && attachmentType.startsWith("image/") ? URL.createObjectURL(attachmentFile) : "";
  const pendingMessage = {
    id: pendingId,
    username: state.messenger.user?.username,
    text,
    attachment: attachmentFile ? {
      name: attachmentFile.name,
      type: attachmentType,
      url: previewUrl,
    } : null,
    timestamp: new Date().toISOString(),
    client_message_id: clientMessageId,
    read: false,
    pending: true,
  };

  state.chatDrafts[roomId] = "";
  chatMessageInput.value = "";
  clearChatAttachment({ preserveUpload: true });
  if (state.selectedRoomId === roomId) {
    appendChatMessageState(pendingMessage);
    appendChatMessageNode(pendingMessage, true);
  }
  chatMessageInput.focus({ preventScroll: true });

  let uploadedAttachment = null;
  void (async () => {
    try {
      let attachment = null;
      if (attachmentFile) {
        const uploadResult = attachmentUpload
          ? await attachmentUpload.promise
          : { attachment: await uploadChatAttachment(attachmentFile, attachmentType), error: null };
        if (uploadResult.error || !uploadResult.attachment) {
          throw uploadResult.error || new Error("첨부 파일을 업로드하지 못했습니다.");
        }
        attachment = uploadResult.attachment;
        uploadedAttachment = attachment;
      }
      const savedMessage = await postChatMessageWithRetry({
        roomId,
        text,
        attachment,
        clientMessageId,
      });
      const room = state.messenger.rooms.find((candidate) => candidate.id === roomId);
      const message = {
        ...savedMessage,
        read: Boolean(savedMessage.read),
      };
      if (state.selectedRoomId === roomId) {
        replaceChatMessageState(pendingId, message);
        replaceChatMessageNode(pendingId, message);
      }
      if (room) {
        room.last_message = message;
        room.updated_at = message.timestamp;
        state.messenger.rooms.sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)));
        if (state.activeList !== "shorts") renderChats();
      }
      if (state.shortMessageNotice?.roomId === roomId) {
        clearShortMessageNotice();
        if (state.activeList === "shorts") renderShortShareBar();
      }
      if (state.activeList === "shorts") renderShortShareBar();
    } catch (error) {
      if (uploadedAttachment) void discardUploadedAttachment(uploadedAttachment);
      if (state.selectedRoomId === roomId) {
        const failedMessage = { ...pendingMessage, pending: false, failed: true };
        replaceChatMessageState(pendingId, failedMessage);
        replaceChatMessageNode(pendingId, failedMessage);
      }
      setAppStatus(error.message, "error");
    } finally {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    }
  })();
}
