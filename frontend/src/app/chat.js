"use strict";

import { CHAT_MESSAGE_MEMORY_LIMIT, CHAT_MESSAGE_PAGE_SIZE, appStore, chatMessageInput, chatMessageList, chatRoom, chatRoomAvatar, chatRoomName, chatRoomPresence, clearChatDraft, formatTime, getChatDraft, normalizeStatusEmoji, openRoomSettingsButton, registerCoreHooks, requestAction, roomSettingsSheet, setAppStatus, state, syncAppStatusForActiveTab } from "./core.js";
import { createRoomAvatar, currentRoom, renderChats, roomParticipantDisplayName, upsertMessengerRoom, upsertRoomAfterMessageDeletion } from "./messenger.js";
import { clearChatAttachment, discardUploadedAttachment, renderChatAttachmentPreview, renderChatAttachmentTray, uploadChatAttachment } from "./attachments.js";
import { formatVoiceDuration } from "./voice.js";
import { chatVirtualRange, createChatVirtualSpacer, measureRenderedChatMessages } from "./chat-virtual.js";
import { loadOlderChatMessages, mergeEntitiesById } from "./app.js";
import { ColorlessImageProcessing } from "./platform/image-processing.js";
import { messageReadReceiptLabel, shouldShowMessageTime } from "./platform/message-display.js";
import { createClientMessageId, postMessageWithRetry } from "./platform/message-retry.js";
import { isPersistedOutboxReplacement } from "./platform/outbox.js";
import { readBoundaryIndex } from "./platform/room-snapshots.js";
import { canMarkSelectedRoomRead, closeMessageReadMenu, completeReplyContext, composerReplySnapshot, composerReplyTarget, decorateMessageRow, registerMessageInteractionHooks, renderComposerContext, resetMessageInteractionState, submitComposerEdit, syncComposerHeight, updateReplyReferences } from "./message-interactions.js";

// Message state, incremental rendering, pagination, retries, and sending.
const CHAT_HISTORY_PAGE_SIZE = typeof CHAT_MESSAGE_PAGE_SIZE === "number" ? CHAT_MESSAGE_PAGE_SIZE : 30;
const CHAT_MEMORY_LIMIT = typeof CHAT_MESSAGE_MEMORY_LIMIT === "number" ? CHAT_MESSAGE_MEMORY_LIMIT : 300;

function currentRoomIdentity() {
  return currentRoom()?.viewer_identity || state.messenger.user || null;
}

function currentRoomUsername() {
  return currentRoomIdentity()?.username || "";
}

function messageSenderDisplayName(room, message) {
  if (message.username === currentRoomUsername()) {
    const identity = currentRoomIdentity();
    return identity?.display_name || identity?.username || message.username;
  }
  if (room?.peer?.username === message.username) {
    return room.peer.display_name || room.peer.username;
  }
  return roomParticipantDisplayName(room, message.username);
}

function roomReadParticipants(room) {
  const participants = [
    currentRoomIdentity(),
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
    read: message.username === currentRoomUsername() ? unreadBy.length === 0 : Boolean(message.read),
  };
}

function applyMessageReaderToCurrentMessages(username, transactionName = "messages.reader", lastReadMessageId = "") {
  const lastReadIndex = readBoundaryIndex(state.messages, state.messageIndexes, lastReadMessageId);
  if (lastReadIndex < 0) return;
  const changedMessages = [];
  appStore.transact(transactionName, () => {
    for (let index = lastReadIndex; index >= 0; index -= 1) {
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
  const username = currentRoomUsername();
  for (let index = messageIndex + 1; index < state.messages.length; index += 1) {
    if (state.messages[index].username === username) return index;
  }
  return -1;
}

function previousOwnMessageIndex(messageIndex) {
  const username = currentRoomUsername();
  for (let index = messageIndex - 1; index >= 0; index -= 1) {
    if (state.messages[index].username === username) return index;
  }
  return -1;
}

function shouldShowMessageReadReceipt(message, messageIndex) {
  if (message.username !== currentRoomUsername()) return false;
  if (message.pending || message.failed) return true;
  const nextIndex = nextOwnMessageIndex(messageIndex);
  if (nextIndex < 0) return true;
  const nextReaders = new Set((state.messages[nextIndex].read_by || []).map((reader) => reader.username));
  return (message.read_by || []).some((reader) => !nextReaders.has(reader.username));
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
  const mine = message.username === currentRoomUsername();
  const row = document.createElement("article");
  row.className = `message-row ${mine ? "mine" : "theirs"}${message.pending ? " pending" : ""}${message.failed ? " failed" : ""}`;
  row.dataset.messageId = message.id;
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  const room = currentRoom();
  if (message.attachment?.url) {
    const attachment = message.attachment;
if (attachment.kind === "voice" && attachment.type?.startsWith("audio/")) {
      bubble.classList.add("voice-message-bubble");
      const voice = document.createElement("div");
      voice.className = "message-voice";
      const audio = document.createElement("audio");
      audio.src = attachment.url;
      audio.controls = true;
      audio.preload = "metadata";
      audio.setAttribute("controlslist", "nodownload noplaybackrate");
      audio.disableRemotePlayback = true;
      audio.setAttribute("aria-label", "음성 메시지 재생");
      const duration = document.createElement("span");
      duration.className = "message-voice-duration";
      duration.textContent = formatVoiceDuration(attachment.duration_ms);
      voice.append(audio, duration);
      bubble.appendChild(voice);
    } else if (attachment.type?.startsWith("image/")) {
      const attachmentLink = document.createElement("a");
      attachmentLink.className = "message-attachment";
      attachmentLink.href = attachment.url;
      attachmentLink.target = "_blank";
      attachmentLink.rel = "noopener";
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
      bubble.appendChild(attachmentLink);

    } else {
      const attachmentLink = document.createElement("a");
      attachmentLink.className = "message-attachment";
      attachmentLink.href = attachment.url;
      attachmentLink.target = "_blank";
      attachmentLink.rel = "noopener";
      attachmentLink.classList.add("message-file");
      const isPdf = attachment.type === "application/pdf";
      attachmentLink.textContent = `${isPdf ? "PDF" : "파일"} · ${attachment.name || "attachment"}`;
      if (!isPdf) attachmentLink.download = attachment.name || "attachment";
      bubble.appendChild(attachmentLink);
    }
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
  decorateMessageRow(row, bubble, meta, message, { mine });
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

function rebuildMessageIndexes() {
  state.messageIndexes.clear();
  for (let index = 0; index < state.messages.length; index += 1) {
    state.messageIndexes.set(state.messages[index].id, index);
  }
}

function setChatMessages(messages) {
  state.messages = messages.length > CHAT_MEMORY_LIMIT
    ? messages.slice(-CHAT_MEMORY_LIMIT)
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
  const overflow = state.messages.length - CHAT_MEMORY_LIMIT;
  if (overflow <= 0) return 0;
  const removedMessages = state.messages.splice(0, overflow);
  for (const message of removedMessages) {
    state.messageHeights.delete(message.id);
    state.messageNodes.delete(message.id);
  }
  rebuildMessageIndexes();
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
  if (index === undefined || !message?.id) return false;
  state.messageHeights.delete(messageId);
  state.messageHeights.delete(message.id);
  state.messages[index] = message;
  state.messageIndexes.delete(messageId);
  state.messageIndexes.set(message.id, index);
  state.messageRevision += 1;
  return true;
}

function applyUpdatedChatMessage(messageId, incomingMessage) {
  const record = state.messageEventJournal.snapshot(
    state.selectedRoomId, incomingMessage, incomingMessage?.mutation_revision,
  );
  if (record?.deleted) return false;
  incomingMessage = record?.message || incomingMessage;
  const index = state.messageIndexes.get(messageId);
  if (!Number.isInteger(index) || !incomingMessage?.id) return false;
  const existing = state.messages[index];
  const currentMutationRevision = Number(existing.mutation_revision);
  const incomingMutationRevision = Number(incomingMessage.mutation_revision);
  if (
    Number.isSafeInteger(currentMutationRevision)
    && Number.isSafeInteger(incomingMutationRevision)
    && incomingMutationRevision < currentMutationRevision
  ) return false;
  const priorReactionState = new Map((existing.reactions || []).map(
    (reaction) => [reaction.emoji, Boolean(reaction.reacted_by_me)],
  ));
  const reactions = incomingMessage.reactions === undefined
    ? (existing.reactions || [])
    : incomingMessage.reactions.map((reaction) => ({
        ...reaction,
        reacted_by_me: reaction.reacted_by_me
          ?? (Array.isArray(reaction.usernames)
            ? reaction.usernames.includes(currentRoomUsername())
            : (priorReactionState.get(reaction.emoji) ?? false)),
      }));
  const message = {
    ...existing,
    ...incomingMessage,
    read_by: incomingMessage.read_by ?? existing.read_by,
    unread_by: incomingMessage.unread_by ?? existing.unread_by,
    reactions,
  };
  const optimistic = isPersistedOutboxReplacement(existing, message);
  if (optimistic) {
    if (existing.retry_data) existing.retry_data.reconciled = true;
    state.chatOutbox.remove(state.selectedRoomId, existing.id);
    Object.assign(message, { pending: false, failed: false });
    delete message.retry_data;
    delete message.preview_url;
    if (existing.preview_url) URL.revokeObjectURL(existing.preview_url);
  }
  if (!replaceChatMessageState(messageId, message)) return false;
  replaceChatMessageNode(messageId, message);
  updateReplyReferences(messageId, message);
  const room = currentRoom();
  if (room?.last_message?.id === messageId) {
    room.last_message = message;
    room.updated_at = message.timestamp || room.updated_at;
    renderChats();
  }
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

async function deleteChatMessage(message) {
  const roomId = state.selectedRoomId;
  const authEpoch = state.authEpoch;
  try {
    const payload = await requestAction("messages.delete", "/messages/delete", {
      method: "POST",
      body: JSON.stringify({ roomId, messageId: message.id }),
    });
    if (state.authEpoch !== authEpoch || state.selectedRoomId !== roomId) return;
    state.messageEventJournal.tombstone(roomId, message.id, payload.mutationRevision, payload.room);
    if (payload.room) upsertRoomAfterMessageDeletion(payload.room);
    if (state.selectedRoomId === roomId && removeChatMessageState(message.id)) {
      updateReplyReferences(message.id);
      renderAllChatMessages();
    }
    renderChats();
    setAppStatus("메시지를 지웠어요.", "success");
  } catch (error) {
    if (state.authEpoch !== authEpoch) return;
    setAppStatus(error.message, "error");
  }
}

function renderAllChatMessages({ scrollToBottom = false, preserveScrollHeight = 0, restoreScrollTop = null } = {}) {
  if (state.chatVirtualFrame !== null) cancelAnimationFrame(state.chatVirtualFrame);
  state.chatVirtualFrame = null;
  const previousScrollTop = chatMessageList.scrollTop;
  const previousScrollHeight = preserveScrollHeight || chatMessageList.scrollHeight;
  const wasNearBottom = chatMessageList.scrollHeight - chatMessageList.clientHeight - previousScrollTop < 80;
  const renderId = state.chatVirtualRenderId + 1;
  state.chatVirtualRenderId = renderId;
  state.chatVirtualAdjusting = true;
  const range = state.messages.length
    ? chatVirtualRange({
        scrollToBottom,
        targetScrollTop: Number.isFinite(restoreScrollTop)
          ? restoreScrollTop
          : (scrollToBottom ? null : previousScrollTop),
      })
    : null;
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
  if (Number.isFinite(restoreScrollTop)) chatMessageList.scrollTop = restoreScrollTop;
  else if (scrollToBottom) chatMessageList.scrollTop = chatMessageList.scrollHeight;
  state.renderedMessageRevision = state.messageRevision;
  state.renderedMessageRoomId = state.selectedRoomId;
  requestAnimationFrame(() => {
    if (state.chatVirtualRenderId !== renderId) return;
    if (state.renderedMessageRoomId !== state.selectedRoomId) {
      requestAnimationFrame(() => {
        if (state.chatVirtualRenderId === renderId) state.chatVirtualAdjusting = false;
      });
      return;
    }
    if (Number.isFinite(restoreScrollTop)) {
      chatMessageList.scrollTop = restoreScrollTop;
    } else if (preserveScrollHeight) {
      chatMessageList.scrollTop = previousScrollTop + chatMessageList.scrollHeight - previousScrollHeight;
    } else {
      chatMessageList.scrollTop = scrollToBottom || wasNearBottom
        ? chatMessageList.scrollHeight
        : previousScrollTop;
    }
    measureRenderedChatMessages();
    if (
      scrollToBottom
      || (!Number.isFinite(restoreScrollTop) && !preserveScrollHeight && wasNearBottom)
    ) chatMessageList.scrollTop = chatMessageList.scrollHeight;
    requestAnimationFrame(() => {
      if (state.chatVirtualRenderId !== renderId || state.renderedMessageRoomId !== state.selectedRoomId) return;
      state.chatVirtualAdjusting = false;
      if (
        state.messagesNextCursor
        && chatMessageList.scrollHeight <= chatMessageList.clientHeight + 1
      ) void loadOlderChatMessages();
    });
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

function appendChatMessageNode(_message, scrollToBottom = false, preservePosition = false) {
  renderAllChatMessages({
    scrollToBottom,
    restoreScrollTop: preservePosition ? chatMessageList.scrollTop : null,
  });
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

function renderChatRoom({ scrollToBottom = false, preserveScrollHeight = 0, restoreScrollTop = null } = {}) {
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
  const hasRoomSettings = isGroupRoom || room.kind === "direct";
  const isInThisRoom = Boolean(presence?.online && presence.active_room_ids?.includes(room.id));
  chatRoom.classList.remove("hidden");
  syncAppStatusForActiveTab();
  openRoomSettingsButton.classList.toggle("hidden", !hasRoomSettings);
  renderChatAttachmentTray();
  renderChatAttachmentPreview();
  const draft = getChatDraft(room.id);
  if (state.composerContext?.mode !== "edit" && chatMessageInput.value !== draft) chatMessageInput.value = draft;
  renderComposerContext();
  syncComposerHeight();
  chatRoomAvatar.replaceChildren(createRoomAvatar(room));
  chatRoomName.textContent = room.name;
  chatRoomPresence.textContent = room.kind === "ticket_listing" || room.kind === "ticket_deal"
    ? [room.ticket?.matchup, room.ticket?.seat].filter(Boolean).join(" · ")
    : isGroupRoom
      ? `${room.participant_count || room.participants?.length || 0}명`
      : (isInThisRoom ? "in chat" : (presence?.online ? "online" : ""));
  if (
    state.renderedMessageRoomId !== room.id
    || state.renderedMessageRevision !== state.messageRevision
  ) {
    renderAllChatMessages({ scrollToBottom, preserveScrollHeight, restoreScrollTop });
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
  if (!roomId || !canMarkSelectedRoomRead(roomId)) return;
  const authEpoch = state.authEpoch;
  window.clearTimeout(state.roomReadTimers.get(roomId));
  const timer = window.setTimeout(() => {
    state.roomReadTimers.delete(roomId);
    if (state.authEpoch !== authEpoch || !canMarkSelectedRoomRead(roomId)) return;
    void requestAction("rooms.mark-read", "/rooms/read", {
      method: "POST",
      body: JSON.stringify({ roomId }),
    }).then((payload) => {
      if (state.authEpoch === authEpoch && state.selectedRoomId === roomId) {
        const lastReadMessageId = payload.room?.last_message?.id || "";
        if (!lastReadMessageId) return;
        applyMessageReaderToCurrentMessages(currentRoomUsername(), "messages.mark-read", lastReadMessageId);
        const room = currentRoom();
        if (room?.last_message?.id === lastReadMessageId) {
          room.unread_count = 0;
          room._local_unread_known = true;
        }
        renderChats();
      }
    }).catch(() => {});
  }, 120);
  state.roomReadTimers.set(roomId, timer);
}

async function loadChatMessages({ markRead = true, scrollToBottom = false, aroundMessageId = "" } = {}) {
  if (!state.selectedRoomId) return;
  const roomId = state.selectedRoomId;
  const authEpoch = state.authEpoch;
  const journalCheckpoint = state.messageEventJournal.checkpoint();
  if (aroundMessageId) state.chatHistoryMode = true;
  state.messagesLoadController?.abort();
  const controller = new AbortController();
  const loadEpoch = state.messagesLoadEpoch + 1;
  state.messagesLoadEpoch = loadEpoch;
  state.messagesLoadController = controller;
  state.messagesInitialLoading = true;
  chatMessageList.setAttribute("aria-busy", "true");
  renderChatRoom();
  try {
    const aroundQuery = aroundMessageId ? `&around=${encodeURIComponent(aroundMessageId)}` : "";
    const payload = await requestAction(
      "messages.load",
      `/messages?room_id=${encodeURIComponent(roomId)}&limit=${CHAT_HISTORY_PAGE_SIZE}${aroundQuery}`,
      { signal: controller.signal },
    );
    if (state.authEpoch !== authEpoch || state.selectedRoomId !== roomId || state.messagesLoadEpoch !== loadEpoch) return;
    const messages = state.messageEventJournal.resolveAll(
      roomId,
      Array.isArray(payload) ? payload : (payload.items || []),
      journalCheckpoint,
    );
    const serverClientMessageIds = new Set();
    for (const message of messages) {
      if (message.client_message_id) serverClientMessageIds.add(message.client_message_id);
    }
    for (const optimisticMessage of state.chatOutbox.list(roomId)) {
      if (serverClientMessageIds.has(optimisticMessage.client_message_id)) {
        if (optimisticMessage.retry_data) optimisticMessage.retry_data.reconciled = true;
        state.chatOutbox.remove(roomId, optimisticMessage.id);
        if (optimisticMessage.preview_url) URL.revokeObjectURL(optimisticMessage.preview_url);
      } else {
        messages.push(optimisticMessage);
      }
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
    if (markRead && !aroundMessageId && canMarkSelectedRoomRead(roomId)) {
      scheduleRoomRead(roomId);
    }
    return true;
  } catch (error) {
    if (error?.name === "AbortError") return;
    setAppStatus(error.message, "error");
    return false;
  } finally {
    if (state.messagesLoadEpoch === loadEpoch) {
      state.messagesLoadController = null;
      state.messagesInitialLoading = false;
      chatMessageList.setAttribute("aria-busy", "false");
      if (state.selectedRoomId === roomId) {
        if (!state.messages.length) state.renderedMessageRoomId = "";
        renderChatRoom();
      }
    }
  }
}

function unloadChatMessages() {
  const roomId = state.selectedRoomId;
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
  for (const message of state.messages) {
    if (message.preview_url && !state.chatOutbox.has(roomId, message.id)) URL.revokeObjectURL(message.preview_url);
  }
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
  const authEpoch = state.authEpoch;
  if (!reset && !cursor) return;
  state.roomMembersLoading.add(roomId);
  try {
    const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    const page = await requestAction(
      "rooms.members",
      `/rooms/${encodeURIComponent(roomId)}/members?limit=50${suffix}`,
    );
    if (state.authEpoch !== authEpoch || state.roomById.get(roomId) !== room) return;
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
  const authEpoch = state.authEpoch;
  if (state.selectedRoomId && state.selectedRoomId !== roomId) {
    unloadChatMessages();
    clearChatAttachment();
  }
  state.chatHistoryMode = Boolean(aroundMessageId);
  resetMessageInteractionState();
  state.selectedRoomId = roomId;
  state.messagesInitialLoading = true;
  state.renderedMessageRoomId = "";
  chatMessageInput.value = getChatDraft(roomId);
  syncComposerHeight();
  renderChatRoom();
  await Promise.all([
    updatePresence(),
    loadRoomMembers(roomId),
    loadChatMessages({ scrollToBottom: !aroundMessageId, aroundMessageId }),
  ]);
  if (focusInput && state.authEpoch === authEpoch && state.selectedRoomId === roomId) {
    chatMessageInput.focus({ preventScroll: true });
  }
}

async function openChatRoomAtMessage(room, messageId) {
  if (!state.roomById.has(room.id)) upsertMessengerRoom(room);
  await openChatRoom(room.id, { aroundMessageId: messageId, focusInput: false });
}

async function jumpToLatestChatMessages() {
  const roomId = state.selectedRoomId;
  if (!roomId) return false;
  const loaded = await loadChatMessages({ markRead: false, scrollToBottom: true });
  if (!loaded || state.selectedRoomId !== roomId) return false;
  state.chatHistoryMode = false;
  renderChatRoom({ scrollToBottom: true });
  scheduleRoomRead(roomId);
  return true;
}

function closeChatRoom() {
  resetMessageInteractionState();
  unloadChatMessages();
  state.selectedRoomId = "";
  state.chatHistoryMode = false;
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
}

function postChatMessageWithRetry(payload, authEpoch = state.authEpoch) {
  return postMessageWithRetry(requestAction, payload, () => state.authEpoch === authEpoch);
}

function reconcileSentMessage(pendingId, message) {
  if (!message?.id) return;
  state.messageEventJournal.snapshot(state.selectedRoomId, message, message.mutation_revision, true);
  if (state.messageIndexes.has(message.id) && message.id !== pendingId) {
    removeChatMessageState(pendingId);
    applyUpdatedChatMessage(message.id, message);
    renderAllChatMessages({ scrollToBottom: true });
    return;
  }
  if (replaceChatMessageState(pendingId, message)) replaceChatMessageNode(pendingId, message);
}

async function deliverPendingMessage(pendingId, pendingMessage, retryData) {
  const { roomId, text, attachmentFile, attachmentType, clientMessageId, replyToMessageId } = retryData;
  const authEpoch = retryData.authEpoch ?? state.authEpoch;
  const active = () => state.authEpoch === authEpoch && !retryData.cancelled;
  const attachmentUpload = retryData.attachmentUpload;
  retryData.attachmentUpload = null;
  let uploadedAttachment = retryData.completedAttachment || null;
  let delivered = false;
  try {
    let attachment = retryData.completedAttachment || null;
    if (attachmentFile && !attachment) {
      const uploadResult = attachmentUpload
        ? await attachmentUpload.promise
        : { attachment: await uploadChatAttachment(attachmentFile, attachmentType), error: null };
      if (uploadResult.error || !uploadResult.attachment) {
        throw uploadResult.error || new Error("첨부 파일을 업로드하지 못했습니다.");
      }
      attachment = uploadResult.attachment;
      uploadedAttachment = attachment;
      retryData.completedAttachment = attachment;
    }
    if (!active()) {
      if (attachment) void discardUploadedAttachment(attachment);
      delivered = true;
      return;
    }
    const messagePayload = { roomId, text, attachment, clientMessageId };
    if (replyToMessageId) messagePayload.replyToMessageId = replyToMessageId;
    const savedMessage = await postChatMessageWithRetry(messagePayload, authEpoch);
    delivered = true;
    if (!active()) return;
    if (uploadedAttachment && savedMessage.attachment?.url !== uploadedAttachment.url) {
      void discardUploadedAttachment(uploadedAttachment);
    }
    const room = state.messenger.rooms.find((candidate) => candidate.id === roomId);
    const message = { ...savedMessage, read: Boolean(savedMessage.read) };
    state.messageEventJournal.snapshot(roomId, message, message.mutation_revision, true);
    state.chatOutbox.remove(roomId, pendingId);
    if (state.selectedRoomId === roomId) reconcileSentMessage(pendingId, message);
    if (room) {
      room.last_message = message;
      room.updated_at = message.timestamp;
      state.messenger.rooms.sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)));
      renderChats();
    }
  } catch (error) {
    if (!state.chatOutbox.has(roomId, pendingId) && retryData.reconciled) {
      delivered = true;
      return;
    }
    if (!active()) {
      if (uploadedAttachment) void discardUploadedAttachment(uploadedAttachment);
      delivered = true;
      return;
    }
    if (!state.chatOutbox.has(roomId, pendingId)) {
      if (uploadedAttachment) void discardUploadedAttachment(uploadedAttachment);
      delivered = true;
      setAppStatus(error.message, "error");
      return;
    }
    const failedMessage = { ...pendingMessage, pending: false, failed: true, retry_data: retryData };
    state.chatOutbox.replace(roomId, pendingId, failedMessage);
    if (state.selectedRoomId === roomId && state.messageIndexes.has(pendingId)) {
      replaceChatMessageState(pendingId, failedMessage);
      replaceChatMessageNode(pendingId, failedMessage);
    }
    setAppStatus(error.message, "error");
  } finally {
    if (delivered && pendingMessage.preview_url) URL.revokeObjectURL(pendingMessage.preview_url);
  }
}

async function retryFailedMessage(message) {
  if (!message?.failed || !message.retry_data || !state.messageIndexes.has(message.id)) return;
  const pendingMessage = { ...message, pending: true, failed: false };
  state.chatOutbox.replace(message.retry_data.roomId, message.id, pendingMessage);
  replaceChatMessageState(message.id, pendingMessage);
  replaceChatMessageNode(message.id, pendingMessage);
  setAppStatus("메시지를 다시 보내고 있어요.");
  await deliverPendingMessage(message.id, pendingMessage, message.retry_data);
}

function sendChatMessage(event) {
  event.preventDefault();
  if (state.composerContext?.mode === "edit") {
    void submitComposerEdit();
    return;
  }
  if (state.voiceRecording || state.voiceRecordingStarting) {
    setAppStatus("녹음을 먼저 중지한 뒤 보내기 버튼을 눌러 주세요.");
    return;
  }
  if (state.chatAttachmentPreparing) {
    setAppStatus("이미지 크기를 줄이는 중이에요. 잠시만 기다려 주세요.");
    return;
  }
  const roomId = state.selectedRoomId;
  const text = (chatMessageInput.value || getChatDraft(roomId)).trim();
  const attachmentFile = state.chatAttachment;
  const attachmentType = state.chatAttachmentType;
  const attachmentUpload = state.chatAttachmentUpload;
  if (!roomId || (!text && !attachmentFile)) return;

  const clientMessageId = createClientMessageId();
  const pendingId = `pending-${clientMessageId}`;
  const replyToMessageId = composerReplyTarget();
  const replyTo = composerReplySnapshot();
  const previewUrl = attachmentFile && (attachmentType.startsWith("image/") || attachmentType.startsWith("audio/"))
    ? URL.createObjectURL(attachmentFile)
    : "";
  const pendingMessage = {
    id: pendingId,
    username: currentRoomUsername(),
    text,
    attachment: attachmentFile ? {
      name: attachmentFile.name,
      type: attachmentType,
      kind: state.chatAttachmentKind,
      duration_ms: state.chatAttachmentDurationMs,
      url: previewUrl,
    } : null,
    timestamp: new Date().toISOString(),
    client_message_id: clientMessageId,
    reply_to: replyTo,
    read: false,
    pending: true,
    preview_url: previewUrl,
  };
  const retryData = {
    roomId,
    text,
    attachmentFile,
    attachmentType,
    attachmentUpload,
    clientMessageId,
    replyToMessageId,
    authEpoch: state.authEpoch,
  };
  pendingMessage.retry_data = retryData;
  state.chatOutbox.put(roomId, pendingMessage);

  clearChatDraft(roomId);
  chatMessageInput.value = "";
  completeReplyContext();
  syncComposerHeight();
  clearChatAttachment({ preserveUpload: true });
  if (state.selectedRoomId === roomId) {
    appendChatMessageState(pendingMessage);
    appendChatMessageNode(pendingMessage, true);
  }
  chatMessageInput.focus({ preventScroll: true });

  void deliverPendingMessage(pendingId, pendingMessage, retryData);
}

registerCoreHooks({ updatePresence });
registerMessageInteractionHooks({
  deleteMessage: deleteChatMessage,
  jumpToLatest: jumpToLatestChatMessages,
  replaceMessage: applyUpdatedChatMessage,
  retryMessage: retryFailedMessage,
  scheduleRoomRead,
});

export {
  addMessageReader,
  appendChatMessageNode,
  appendChatMessageState,
  applyMessageReaderToCurrentMessages,
  applyUpdatedChatMessage,
  closeChatRoom,
  createClientMessageId,
  loadChatMessages,
  openChatRoom,
  openChatRoomAtMessage,
  postChatMessageWithRetry,
  rebuildMessageIndexes,
  removeChatMessageState,
  renderAllChatMessages,
  renderChatRoom,
  scheduleChatVirtualRender,
  scheduleRoomRead,
  sendChatMessage,
  updateReplyReferences,
  updatePresence,
};
