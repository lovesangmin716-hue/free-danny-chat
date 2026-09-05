"use strict";

import {
  cancelChatComposerContextButton,
  chatAttachmentButton,
  chatComposerContext,
  chatComposerContextCopy,
  chatComposerContextTitle,
  chatMessageForm,
  chatMessageInput,
  chatMessageList,
  chatNewMessagesButton,
  chatRoom,
  closeMessageActionButton,
  getChatDraft,
  getDisplayName,
  messageActionDialog,
  messageActionReactions,
  messageActionStatus,
  messageActionSummary,
  messageReadMenu,
  messageReadMenuCopy,
  messageReadMenuTitle,
  registerCoreHooks,
  requestAction,
  setAppStatus,
  setChatDraft,
  state,
} from "./core.js";

// Accessible message actions, composer context, persisted drafts, and viewport awareness.
const MESSAGE_READ_SWIPE_THRESHOLD = 34;
const CHAT_BOTTOM_THRESHOLD = 80;
const COMPOSER_MAX_HEIGHT = 116;
let messageReadSwipe = null;
let suppressMessageClick = false;
let actionMessageId = "";
let actionOpener = null;
let composerSubmitting = false;
let initialized = false;
const replySourceRevisions = new Map();

const hooks = {
  deleteMessage: null,
  jumpToLatest: null,
  replaceMessage: null,
  retryMessage: null,
  scheduleRoomRead: null,
};

function registerMessageInteractionHooks(nextHooks) {
  for (const [name, hook] of Object.entries(nextHooks)) {
    if (!(name in hooks) || typeof hook !== "function") {
      throw new TypeError(`Unknown message interaction hook: ${name}`);
    }
    hooks[name] = hook;
  }
}

function selectedRoom() {
  return state.roomById.get(state.selectedRoomId)
    || state.messenger.rooms.find((room) => room.id === state.selectedRoomId)
    || null;
}

function currentRoomUsername() {
  const room = selectedRoom();
  return room?.viewer_identity?.username || state.messenger.user?.username || "";
}

function messageById(messageId) {
  const index = state.messageIndexes.get(messageId);
  return Number.isInteger(index) ? state.messages[index] : null;
}

function messagePreview(message) {
  if (message?.text) return message.text.replace(/\s+/g, " ").trim();
  const attachment = message?.attachment;
  if (attachment?.kind === "voice") return "음성 메시지";
  if (attachment?.type === "application/pdf") return "PDF";
  if (attachment?.type?.startsWith("image/")) return "사진";
  return attachment?.name || "첨부 파일";
}

function messageSenderName(message) {
  if (!message) return "메시지";
  if (message.username === currentRoomUsername()) {
    const identity = selectedRoom()?.viewer_identity || state.messenger.user;
    return getDisplayName(identity) || message.username;
  }
  if (message.display_name) return message.display_name;
  const room = selectedRoom();
  const participant = [room?.peer, ...(room?.participants || [])]
    .find((candidate) => candidate?.username === message.username);
  return getDisplayName(participant) || message.username || "메시지";
}

function replySnapshot(message) {
  return {
    id: message.id,
    username: message.username,
    display_name: messageSenderName(message),
    text: message.text || "",
    attachment_kind: message.attachment?.kind
      || (message.attachment?.type?.startsWith("image/") ? "image" : (message.attachment ? "file" : "")),
    deleted: false,
  };
}

function updateReplyReferences(messageId, sourceMessage = null) {
  if (!messageId) return false;
  const incomingRevision = Number(sourceMessage?.mutation_revision);
  const priorRevision = replySourceRevisions.get(messageId);
  if (
    sourceMessage
    && Number.isSafeInteger(incomingRevision)
    && Number.isSafeInteger(priorRevision)
    && incomingRevision < priorRevision
  ) return false;
  replySourceRevisions.set(
    messageId,
    sourceMessage && Number.isSafeInteger(incomingRevision) ? incomingRevision : Number.MAX_SAFE_INTEGER,
  );
  const reply = sourceMessage ? replySnapshot(sourceMessage) : { id: messageId, deleted: true };
  const references = state.messages.filter((message) => message.reply_to?.id === messageId);
  for (const message of references) {
    hooks.replaceMessage?.(message.id, { ...message, reply_to: reply });
  }
  if (state.composerContext?.mode === "reply" && state.composerContext.messageId === messageId) {
    if (sourceMessage) {
      state.composerContext = { ...state.composerContext, snapshot: reply };
      renderComposerContext();
    } else {
      cancelComposerContext({ focus: false });
      setAppStatus("답장하려던 원본 메시지가 삭제되어 답장을 취소했어요.");
    }
  }
  return Boolean(references.length);
}

function replyPreview(reply) {
  if (!reply || reply.deleted) return "삭제된 메시지";
  if (reply.text) return reply.text.replace(/\s+/g, " ").trim();
  return ({ voice: "음성 메시지", image: "사진", photo: "사진", file: "첨부 파일" })[reply.attachment_kind]
    || "첨부 메시지";
}

function createReplyQuote(reply) {
  if (!reply) return null;
  const quote = document.createElement("div");
  quote.className = "message-reply-quote";
  const sender = document.createElement("strong");
  sender.textContent = reply.deleted ? "원본 메시지" : (reply.display_name || reply.username || "원본 메시지");
  const copy = document.createElement("span");
  copy.textContent = replyPreview(reply);
  quote.append(sender, copy);
  return quote;
}

function decorateMessageRow(row, bubble, meta, message, { mine = false } = {}) {
  const quote = createReplyQuote(message.reply_to);
  if (quote) bubble.prepend(quote);
  if (message.edited_at) {
    const edited = document.createElement("span");
    edited.className = "message-edited";
    edited.textContent = "수정됨";
    meta.prepend(edited);
  }

  const reactions = (message.reactions || []).filter((reaction) => Number(reaction.count) > 0);
  if (reactions.length) {
    const reactionList = document.createElement("div");
    reactionList.className = "message-reactions";
    reactionList.setAttribute("aria-label", "메시지 반응");
    for (const reaction of reactions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "message-reaction-chip";
      button.dataset.messageId = message.id;
      button.dataset.messageReaction = reaction.emoji;
      button.setAttribute("aria-pressed", String(Boolean(reaction.reacted_by_me)));
      button.setAttribute("aria-label", `${reaction.emoji} 반응 ${reaction.count}개${reaction.reacted_by_me ? ", 내가 반응함" : ""}`);
      button.textContent = `${reaction.emoji} ${reaction.count}`;
      reactionList.appendChild(button);
    }
    row.insertBefore(reactionList, meta);
  }

  const actionButton = document.createElement("button");
  actionButton.type = "button";
  actionButton.className = "message-action-trigger";
  actionButton.dataset.messageId = message.id;
  actionButton.setAttribute("aria-haspopup", "dialog");
  actionButton.setAttribute("aria-label", `${mine ? "내" : messageSenderName(message)} 메시지 작업`);
  actionButton.textContent = "⋯";
  row.appendChild(actionButton);
}

function syncComposerHeight() {
  chatMessageInput.style.height = "auto";
  chatMessageInput.style.height = `${Math.min(chatMessageInput.scrollHeight, COMPOSER_MAX_HEIGHT)}px`;
}

function renderComposerContext() {
  const context = state.composerContext;
  chatComposerContext.classList.toggle("hidden", !context);
  chatAttachmentButton.disabled = Boolean(context?.mode === "edit") || state.voiceRecordingStarting;
  chatMessageInput.setAttribute(
    "aria-label",
    context?.mode === "edit" ? "메시지 수정" : (context?.mode === "reply" ? "답장 입력" : "메시지 입력"),
  );
  if (!context) {
    chatComposerContextTitle.textContent = "";
    chatComposerContextCopy.textContent = "";
    return;
  }
  chatComposerContextTitle.textContent = context.mode === "edit"
    ? "메시지 수정"
    : `${context.snapshot.display_name || context.snapshot.username || "상대방"}에게 답장`;
  chatComposerContextCopy.textContent = replyPreview(context.snapshot);
}

function cancelComposerContext({ focus = true, restoreDraft = true } = {}) {
  const context = state.composerContext;
  if (!context) return;
  state.composerContext = null;
  if (context.mode === "edit" && restoreDraft) {
    chatMessageInput.value = context.previousDraft || "";
    setChatDraft(state.selectedRoomId, chatMessageInput.value);
  }
  renderComposerContext();
  syncComposerHeight();
  if (focus) chatMessageInput.focus({ preventScroll: true });
}

function beginReply(message) {
  if (!message || message.pending || message.failed) return;
  if (state.composerContext?.mode === "edit") cancelComposerContext({ focus: false });
  state.composerContext = { mode: "reply", messageId: message.id, snapshot: replySnapshot(message) };
  renderComposerContext();
  chatMessageInput.focus({ preventScroll: true });
}

function beginEdit(message) {
  if (!message || (!message.text && !message.attachment) || message.pending || message.failed || message.username !== currentRoomUsername()) return;
  if (state.chatAttachment || state.voiceRecording || state.chatAttachmentPreparing) {
    setAppStatus("첨부 또는 녹음을 취소한 뒤 메시지를 수정해 주세요.");
    return;
  }
  const previousDraft = chatMessageInput.value || getChatDraft(state.selectedRoomId);
  state.composerContext = {
    mode: "edit",
    messageId: message.id,
    snapshot: replySnapshot(message),
    hasAttachment: Boolean(message.attachment),
    previousDraft,
  };
  chatMessageInput.value = message.text;
  renderComposerContext();
  syncComposerHeight();
  chatMessageInput.focus({ preventScroll: true });
  chatMessageInput.setSelectionRange(chatMessageInput.value.length, chatMessageInput.value.length);
}

function composerReplyTarget() {
  return state.composerContext?.mode === "reply" ? state.composerContext.messageId : "";
}

function composerReplySnapshot() {
  return state.composerContext?.mode === "reply" ? state.composerContext.snapshot : null;
}

async function submitComposerEdit() {
  const context = state.composerContext;
  if (context?.mode !== "edit") return false;
  const text = chatMessageInput.value.trim();
  if (!text && !context.hasAttachment) {
    setAppStatus("첨부가 없는 메시지는 내용을 비울 수 없어요.", "error");
    return true;
  }
  if (composerSubmitting) return true;
  const authEpoch = state.authEpoch;
  const roomId = state.selectedRoomId;
  const controller = new AbortController();
  state.composerEditController?.abort();
  state.composerEditController = controller;
  composerSubmitting = true;
  chatMessageInput.disabled = true;
  try {
    const payload = await requestAction("messages.edit", "/messages/edit", {
      method: "POST",
      body: JSON.stringify({ roomId, messageId: context.messageId, text }),
      signal: controller.signal,
    });
    if (controller.signal.aborted || state.authEpoch !== authEpoch || state.selectedRoomId !== roomId) return true;
    const message = payload?.message || payload;
    hooks.replaceMessage?.(context.messageId, message);
    cancelComposerContext({ restoreDraft: true });
    setAppStatus("메시지를 수정했어요.", "success");
  } catch (error) {
    if (controller.signal.aborted || state.authEpoch !== authEpoch || error?.name === "AbortError") return true;
    setAppStatus(error.message, "error");
  } finally {
    if (state.composerEditController === controller) {
      state.composerEditController = null;
      composerSubmitting = false;
      chatMessageInput.disabled = false;
      if (state.authEpoch === authEpoch && state.selectedRoomId === roomId) chatMessageInput.focus({ preventScroll: true });
    }
  }
  return true;
}

function completeReplyContext() {
  if (state.composerContext?.mode !== "reply") return;
  state.composerContext = null;
  renderComposerContext();
}

function resetMessageInteractionState() {
  state.composerEditController?.abort();
  state.composerEditController = null;
  composerSubmitting = false;
  chatMessageInput.disabled = false;
  replySourceRevisions.clear();
  closeMessageActionDialog({ restoreFocus: false });
  closeMessageReadMenu();
  chatRoom.classList.add("hidden");
  chatMessageList.replaceChildren();
  state.composerContext = null;
  state.chatNewMessageCount = 0;
  renderComposerContext();
  renderNewMessageButton();
}

function actionButtons() {
  return [...messageActionDialog.querySelectorAll("[data-message-action]")];
}

function updateActionDialog() {
  const message = messageById(actionMessageId);
  if (!message) {
    closeMessageActionDialog({ restoreFocus: false });
    return;
  }
  const mine = message.username === currentRoomUsername();
  const persisted = !message.pending && !message.failed && !String(message.id).startsWith("pending-");
  messageActionSummary.textContent = `${messageSenderName(message)} · ${messagePreview(message)}`;
  messageActionStatus.textContent = "";
  const visibility = {
    reply: persisted,
    copy: Boolean(message.text),
    edit: persisted && mine && Boolean(message.text || message.attachment),
    read: persisted && mine,
    retry: Boolean(message.failed && mine && message.retry_data),
    delete: persisted && mine,
  };
  for (const button of actionButtons()) button.classList.toggle("hidden", !visibility[button.dataset.messageAction]);
  messageActionReactions.classList.toggle("hidden", !persisted);
  const reacted = new Set((message.reactions || [])
    .filter((reaction) => reaction.reacted_by_me)
    .map((reaction) => reaction.emoji));
  for (const button of messageActionReactions.querySelectorAll("[data-message-reaction]")) {
    button.setAttribute("aria-pressed", String(reacted.has(button.dataset.messageReaction)));
  }
}

function openMessageActionDialog(messageId, opener = null) {
  const message = messageById(messageId);
  if (!message) return;
  actionMessageId = messageId;
  actionOpener = opener || document.activeElement;
  updateActionDialog();
  messageActionDialog.classList.remove("hidden");
  requestAnimationFrame(() => {
    const first = [...messageActionDialog.querySelectorAll("button:not(:disabled):not(.hidden)")]
      .find((button) => button.getClientRects().length > 0);
    first?.focus({ preventScroll: true });
  });
}

function closeMessageActionDialog({ restoreFocus = true } = {}) {
  if (!messageActionDialog || messageActionDialog.classList.contains("hidden")) return;
  messageActionDialog.classList.add("hidden");
  actionMessageId = "";
  const opener = actionOpener;
  actionOpener = null;
  if (restoreFocus && opener?.isConnected) opener.focus({ preventScroll: true });
}

async function copyMessageText(message) {
  if (!message?.text) return;
  try {
    await navigator.clipboard.writeText(message.text);
  } catch (_) {
    const input = document.createElement("textarea");
    input.value = message.text;
    input.setAttribute("readonly", "");
    input.className = "sr-only";
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  }
  messageActionStatus.textContent = "메시지를 복사했어요.";
}

async function toggleMessageReaction(messageId, emoji) {
  const message = messageById(messageId);
  if (!message || message.pending || message.failed || !emoji) return;
  const reacted = !Boolean((message.reactions || [])
    .find((reaction) => reaction.emoji === emoji)?.reacted_by_me);
  const authEpoch = state.authEpoch;
  const roomId = state.selectedRoomId;
  try {
    const payload = await requestAction(`messages.react:${messageId}:${emoji}`, "/messages/reactions", {
      method: "POST",
      body: JSON.stringify({ roomId, messageId, emoji, reacted }),
    }, { key: `messages.react:${messageId}:${emoji}`, policy: "join" });
    if (state.authEpoch !== authEpoch || state.selectedRoomId !== roomId) return;
    const updated = payload?.message || payload;
    if (updated?.id) hooks.replaceMessage?.(messageId, updated);
    if (actionMessageId === messageId) updateActionDialog();
  } catch (error) {
    if (state.authEpoch !== authEpoch) return;
    setAppStatus(error.message, "error");
  }
}

function applyReactionEvent(message, payload, viewerUsername = currentRoomUsername()) {
  if (!message || !payload?.emoji) return message;
  const currentRevision = Number(message.mutation_revision);
  const incomingRevision = Number(payload.mutationRevision ?? payload.mutation_revision);
  if (Number.isSafeInteger(currentRevision) && Number.isSafeInteger(incomingRevision) && incomingRevision < currentRevision) {
    return message;
  }
  const reactions = [...(message.reactions || [])];
  const index = reactions.findIndex((reaction) => reaction.emoji === payload.emoji);
  const current = index >= 0 ? reactions[index] : { emoji: payload.emoji, count: 0, reacted_by_me: false };
  const next = {
    ...current,
    count: Math.max(0, Number(payload.count) || 0),
    reacted_by_me: payload.actorUsername === viewerUsername
      ? Boolean(payload.reacted)
      : Boolean(current.reacted_by_me),
  };
  if (next.count > 0 && index >= 0) reactions[index] = next;
  else if (next.count > 0) reactions.push(next);
  else if (index >= 0) reactions.splice(index, 1);
  return {
    ...message,
    reactions,
    ...(Number.isSafeInteger(incomingRevision) ? { mutation_revision: incomingRevision } : {}),
  };
}

async function handleMessageAction(action) {
  const message = messageById(actionMessageId);
  if (!message) return;
  if (action === "reply") {
    closeMessageActionDialog({ restoreFocus: false });
    beginReply(message);
  } else if (action === "copy") {
    await copyMessageText(message);
  } else if (action === "edit") {
    closeMessageActionDialog({ restoreFocus: false });
    beginEdit(message);
  } else if (action === "read") {
    const names = (message.unread_by || []).map((reader) => reader.display_name || reader.username).filter(Boolean);
    messageActionStatus.textContent = names.length ? `안 읽은 사람: ${names.join(", ")}` : "모두 읽었어요.";
  } else if (action === "retry") {
    closeMessageActionDialog({ restoreFocus: false });
    await hooks.retryMessage?.(message);
  } else if (action === "delete") {
    if (!window.confirm("이 메시지를 삭제할까요?")) return;
    closeMessageActionDialog({ restoreFocus: false });
    await hooks.deleteMessage?.(message);
  }
}

function handleMessageListClick(event) {
  const reaction = event.target.closest?.("[data-message-reaction][data-message-id]");
  if (reaction) {
    event.preventDefault();
    void toggleMessageReaction(reaction.dataset.messageId, reaction.dataset.messageReaction);
    return true;
  }
  const trigger = event.target.closest?.(".message-action-trigger");
  if (!trigger) return false;
  event.preventDefault();
  openMessageActionDialog(trigger.dataset.messageId, trigger);
  return true;
}

function handleMessageActionKeydown(event) {
  if (messageActionDialog.classList.contains("hidden")) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeMessageActionDialog();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...messageActionDialog.querySelectorAll("button:not(:disabled):not(.hidden)")]
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
  messageReadMenuCopy.textContent = unreadNames.length ? unreadNames.join(", ") : "모두 읽었어요.";
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
  if (event.target.closest?.("audio, button, a, input, textarea")) return;
  const row = event.target.closest?.(".message-row");
  const message = row ? messageById(row.dataset.messageId) : null;
  if (!row || !message || message.pending || message.failed) return;
  closeMessageReadMenu();
  messageReadSwipe = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, message, row, revealed: false };
  row.setPointerCapture?.(event.pointerId);
}

function updateMessageReadSwipe(event) {
  const swipe = messageReadSwipe;
  if (!swipe || swipe.pointerId !== event.pointerId) return;
  const deltaX = event.clientX - swipe.startX;
  const deltaY = event.clientY - swipe.startY;
  if (Math.abs(deltaX) <= Math.abs(deltaY) * 1.15) return;
  event.preventDefault();
  if (!swipe.revealed && deltaX <= -MESSAGE_READ_SWIPE_THRESHOLD) {
    swipe.revealed = true;
    swipe.row.classList.add("showing-readers");
    openMessageReadMenu(swipe.message, swipe.row);
  }
}

function finishMessageReadSwipe(event) {
  const swipe = messageReadSwipe;
  if (!swipe || (event?.pointerId !== undefined && swipe.pointerId !== event.pointerId)) return;
  if (swipe.revealed) {
    suppressMessageClick = true;
    window.setTimeout(() => { suppressMessageClick = false; }, 0);
  }
  messageReadSwipe = null;
  swipe.row.classList.remove("showing-readers");
  if (swipe.row.hasPointerCapture?.(swipe.pointerId)) swipe.row.releasePointerCapture(swipe.pointerId);
  closeMessageReadMenu();
}

function suppressMessageReadContextMenu(event) {
  const row = event.target.closest?.(".message-row");
  if (!row) return;
  event.preventDefault();
  openMessageActionDialog(row.dataset.messageId, row.querySelector(".message-action-trigger"));
}

function suppressClickAfterMessageSwipe(event) {
  if (!suppressMessageClick) return;
  event.preventDefault();
  event.stopPropagation();
}

function chatIsNearBottom() {
  return chatMessageList.scrollHeight - chatMessageList.clientHeight - chatMessageList.scrollTop < CHAT_BOTTOM_THRESHOLD;
}

function canMarkSelectedRoomRead(roomId = state.selectedRoomId) {
  return Boolean(
    roomId
    && roomId === state.selectedRoomId
    && !document.hidden
    && !chatRoom.classList.contains("hidden")
    && !state.chatHistoryMode
    && chatIsNearBottom()
  );
}

function renderNewMessageButton() {
  const count = state.chatNewMessageCount;
  chatNewMessagesButton.classList.toggle("hidden", count < 1 && !state.chatHistoryMode);
  chatNewMessagesButton.textContent = state.chatHistoryMode
    ? (count ? `최신 메시지로 이동 · ${count}개` : "최신 메시지로 이동")
    : (count > 1 ? `새 메시지 ${count}개` : "새 메시지");
  chatNewMessagesButton.setAttribute("aria-label", state.chatHistoryMode
    ? `최신 메시지로 이동${count ? `, 새 메시지 ${count}개` : ""}`
    : (count > 1 ? `새 메시지 ${count}개로 이동` : "새 메시지로 이동"));
}

function noteIncomingMessage(autoScroll) {
  if (autoScroll) state.chatNewMessageCount = 0;
  else state.chatNewMessageCount += 1;
  renderNewMessageButton();
}

async function jumpToLatestMessages() {
  if (state.chatHistoryMode) await hooks.jumpToLatest?.();
  else chatMessageList.scrollTop = chatMessageList.scrollHeight;
  state.chatNewMessageCount = 0;
  renderNewMessageButton();
  hooks.scheduleRoomRead?.(state.selectedRoomId);
  chatMessageInput.focus({ preventScroll: true });
}

function handleChatViewportChange() {
  if (!canMarkSelectedRoomRead()) return;
  if (state.chatNewMessageCount) {
    state.chatNewMessageCount = 0;
    renderNewMessageButton();
  }
  hooks.scheduleRoomRead?.(state.selectedRoomId);
}

function handleComposerInput() {
  if (state.selectedRoomId && state.composerContext?.mode !== "edit") {
    setChatDraft(state.selectedRoomId, chatMessageInput.value);
  }
  syncComposerHeight();
}

function handleComposerKeydown(event) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  chatMessageForm.requestSubmit();
}

function initializeMessageInteractions() {
  if (initialized) return;
  initialized = true;
  cancelChatComposerContextButton.addEventListener("click", () => cancelComposerContext());
  chatNewMessagesButton.addEventListener("click", () => void jumpToLatestMessages());
  closeMessageActionButton.addEventListener("click", () => closeMessageActionDialog());
  messageActionDialog.addEventListener("click", (event) => {
    if (event.target === messageActionDialog) {
      closeMessageActionDialog();
      return;
    }
    const reaction = event.target.closest?.("[data-message-reaction]");
    if (reaction) {
      void toggleMessageReaction(actionMessageId, reaction.dataset.messageReaction);
      return;
    }
    const action = event.target.closest?.("[data-message-action]")?.dataset.messageAction;
    if (action) void handleMessageAction(action);
  });
  messageActionDialog.addEventListener("keydown", handleMessageActionKeydown);
  renderComposerContext();
  renderNewMessageButton();
}

registerCoreHooks({ resetMessageInteractions: resetMessageInteractionState });

export {
  applyReactionEvent,
  beginMessageReadSwipe,
  canMarkSelectedRoomRead,
  cancelComposerContext,
  closeMessageActionDialog,
  closeMessageReadMenu,
  completeReplyContext,
  composerReplySnapshot,
  composerReplyTarget,
  decorateMessageRow,
  finishMessageReadSwipe,
  handleChatViewportChange,
  handleComposerInput,
  handleComposerKeydown,
  handleMessageListClick,
  initializeMessageInteractions,
  noteIncomingMessage,
  openMessageActionDialog,
  registerMessageInteractionHooks,
  renderComposerContext,
  resetMessageInteractionState,
  submitComposerEdit,
  suppressClickAfterMessageSwipe,
  suppressMessageReadContextMenu,
  syncComposerHeight,
  toggleMessageReaction,
  updateMessageReadSwipe,
  updateReplyReferences,
};
