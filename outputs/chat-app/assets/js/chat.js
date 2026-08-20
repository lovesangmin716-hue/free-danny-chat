"use strict";

// Message state, incremental rendering, pagination, retries, and sending.
const MESSAGE_TIME_CLUSTER_MS = 5 * 60 * 1000;

function shouldShowMessageTime(message, nextMessage) {
  if (!nextMessage || nextMessage.username !== message.username) return true;
  const timestamp = Date.parse(message.timestamp);
  const nextTimestamp = Date.parse(nextMessage.timestamp);
  if (!Number.isFinite(timestamp) || !Number.isFinite(nextTimestamp) || nextTimestamp < timestamp) return true;
  return nextTimestamp - timestamp > MESSAGE_TIME_CLUSTER_MS;
}

function createChatMessageRow(message, nextMessage = null) {
  const mine = message.username === state.messenger.user?.username;
  const row = document.createElement("article");
  row.className = `message-row ${mine ? "mine" : "theirs"}${message.pending ? " pending" : ""}${message.failed ? " failed" : ""}`;
  row.dataset.messageId = message.id;
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  const room = currentRoom();
  if (!mine && room?.kind === "group") {
    const sender = document.createElement("strong");
    sender.className = "message-sender";
    sender.textContent = roomParticipantDisplayName(room, message.username);
    bubble.appendChild(sender);
  }
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
    read.className = message.failed ? "message-unread" : (message.read ? "message-read" : "message-unread");
    if (message.pending) {
      read.textContent = "보내는 중";
    } else if (message.failed) {
      read.textContent = "전송 실패";
    } else if (room?.kind === "group") {
      read.textContent = message.read ? "모두 읽음" : "안 읽음";
    } else {
      read.textContent = message.read ? "읽음" : "안 읽음";
    }
    meta.appendChild(read);
  }
  const time = document.createElement("time");
  time.textContent = formatTime(message.timestamp);
  time.classList.toggle("hidden", !shouldShowMessageTime(message, nextMessage));
  meta.appendChild(time);
  row.append(bubble, meta);
  return row;
}

function syncMessageTimeVisibility(messageIndex) {
  if (!Number.isInteger(messageIndex) || messageIndex < 0 || messageIndex >= state.messages.length) return;
  const message = state.messages[messageIndex];
  const row = state.messageNodes.get(message.id);
  const time = row?.querySelector("time");
  if (!time) return;
  time.classList.toggle("hidden", !shouldShowMessageTime(message, state.messages[messageIndex + 1]));
}

function closeMessageReadMenu() {
  messageReadMenu.classList.add("hidden");
}

function openMessageReadMenu(message, clientX, clientY) {
  const unreadNames = (message.unread_by || [])
    .map((reader) => reader.display_name || reader.username)
    .filter(Boolean);
  messageReadMenuTitle.textContent = message.read ? "모두 읽음" : "아직 읽지 않음";
  messageReadMenuCopy.textContent = unreadNames.length
    ? unreadNames.join(", ")
    : (message.read ? "모든 참여자가 이 메시지를 읽었어요." : "읽음 정보를 불러오는 중이에요.");
  messageReadMenu.classList.remove("hidden");
  const width = messageReadMenu.offsetWidth;
  const height = messageReadMenu.offsetHeight;
  messageReadMenu.style.left = `${Math.max(8, Math.min(clientX, window.innerWidth - width - 8))}px`;
  messageReadMenu.style.top = `${Math.max(8, Math.min(clientY, window.innerHeight - height - 8))}px`;
}

function showMessageReadMenuFromContext(event) {
  const row = event.target.closest?.(".message-row.mine");
  const room = currentRoom();
  if (!row || room?.kind !== "group") return;
  const message = state.messages[state.messageIndexes.get(row.dataset.messageId)];
  if (!message || message.pending || message.failed) return;
  event.preventDefault();
  openMessageReadMenu(message, event.clientX, event.clientY);
}

function rebuildMessageIndexes() {
  state.messageIndexes.clear();
  for (let index = 0; index < state.messages.length; index += 1) {
    state.messageIndexes.set(state.messages[index].id, index);
  }
}

function setChatMessages(messages) {
  state.messages = messages;
  rebuildMessageIndexes();
  state.messageRevision += 1;
}

function appendChatMessageState(message) {
  if (!message?.id || state.messageIndexes.has(message.id)) return false;
  state.messageIndexes.set(message.id, state.messages.length);
  state.messages.push(message);
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

function renderAllChatMessages({ scrollToBottom = false, preserveScrollHeight = 0 } = {}) {
  const previousScrollTop = chatMessageList.scrollTop;
  const previousScrollHeight = preserveScrollHeight || chatMessageList.scrollHeight;
  const wasNearBottom = chatMessageList.scrollHeight - chatMessageList.clientHeight - previousScrollTop < 80;
  state.messageNodes.clear();
  chatMessageList.replaceChildren();
  if (!state.messages.length) {
    const empty = document.createElement("p");
    empty.className = "chat-empty";
    empty.textContent = "첫 메시지를 보내 보세요.";
    chatMessageList.appendChild(empty);
  } else {
    const fragment = document.createDocumentFragment();
    for (let index = 0; index < state.messages.length; index += 1) {
      const message = state.messages[index];
      const row = createChatMessageRow(message, state.messages[index + 1]);
      state.messageNodes.set(message.id, row);
      fragment.appendChild(row);
    }
    chatMessageList.appendChild(fragment);
  }
  state.renderedMessageRevision = state.messageRevision;
  state.renderedMessageRoomId = state.selectedRoomId;
  requestAnimationFrame(() => {
    if (preserveScrollHeight) {
      chatMessageList.scrollTop = previousScrollTop + chatMessageList.scrollHeight - previousScrollHeight;
    } else {
      chatMessageList.scrollTop = scrollToBottom || wasNearBottom
        ? chatMessageList.scrollHeight
        : previousScrollTop;
    }
    if (
      state.messagesNextCursor
      && chatMessageList.scrollHeight <= chatMessageList.clientHeight + 1
    ) void loadOlderChatMessages();
  });
}

function appendChatMessageNode(message, scrollToBottom = false) {
  chatMessageList.querySelector(".chat-empty")?.remove();
  const row = createChatMessageRow(message);
  state.messageNodes.set(message.id, row);
  chatMessageList.appendChild(row);
  syncMessageTimeVisibility(state.messages.length - 2);
  state.renderedMessageRevision = state.messageRevision;
  state.renderedMessageRoomId = state.selectedRoomId;
  if (scrollToBottom) chatMessageList.scrollTop = chatMessageList.scrollHeight;
}

function replaceChatMessageNode(messageId, message) {
  const currentRow = state.messageNodes.get(messageId);
  if (!currentRow) {
    renderAllChatMessages();
    return;
  }
  const messageIndex = state.messageIndexes.get(message.id);
  const nextRow = createChatMessageRow(
    message,
    Number.isInteger(messageIndex) ? state.messages[messageIndex + 1] : null,
  );
  currentRow.replaceWith(nextRow);
  state.messageNodes.delete(messageId);
  state.messageNodes.set(message.id, nextRow);
  syncMessageTimeVisibility(messageIndex - 1);
  state.renderedMessageRevision = state.messageRevision;
}

function renderChatRoom({ scrollToBottom = false, preserveScrollHeight = 0 } = {}) {
  const room = currentRoom();
  if (!room) {
    chatRoom.classList.add("hidden");
    openRoomSettingsButton.classList.add("hidden");
    roomSettingsSheet.classList.add("hidden");
    return;
  }
  const presence = room.peer?.presence;
  const isGroupRoom = room.kind === "group";
  const isInThisRoom = Boolean(presence?.online && presence.active_room_ids?.includes(room.id));
  chatRoom.classList.remove("hidden");
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

async function loadChatMessages({ markRead = true, scrollToBottom = false } = {}) {
  if (!state.selectedRoomId) return;
  const roomId = state.selectedRoomId;
  try {
    const payload = await requestAction(
      "messages.load",
      `/messages?room_id=${encodeURIComponent(roomId)}&limit=30`,
    );
    if (state.selectedRoomId !== roomId) return;
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
    if (markRead && state.selectedRoomId === roomId) {
      void api("/rooms/read", {
        method: "POST",
        body: JSON.stringify({ roomId }),
      }).catch(() => {});
    }
  } catch (error) {
    setAppStatus(error.message, "error");
  }
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

async function openChatRoom(roomId) {
  state.selectedRoomId = roomId;
  setChatMessages([]);
  state.messagesNextCursor = "";
  state.messagesLoadingOlder = false;
  state.renderedMessageRoomId = "";
  chatMessageInput.value = state.chatDrafts[roomId] || "";
  renderChatRoom();
  await Promise.all([
    updatePresence(),
    loadRoomMembers(roomId),
    loadChatMessages({ scrollToBottom: true }),
  ]);
  chatMessageInput.focus({ preventScroll: true });
}

function closeChatRoom() {
  const wasReplyingToShortNotice = state.activeList === "shorts" && state.shortMessagePaused;
  state.selectedRoomId = "";
  setChatMessages([]);
  state.messagesNextCursor = "";
  state.messagesLoadingOlder = false;
  state.renderedMessageRoomId = "";
  closeMessageReadMenu();
  clearChatAttachment();
  if (state.roomImageProcessing) {
    state.roomImageSelectionId += 1;
    state.roomImageProcessing = false;
    ColorlessImageProcessing.cancel("room-image");
  }
  chatRoom.classList.add("hidden");
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
