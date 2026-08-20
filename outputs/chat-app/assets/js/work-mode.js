"use strict";

// Full-screen, keyboard-first incoming message view.
const workModeToggle = document.getElementById("work-mode-toggle");
const workModeScreen = document.getElementById("work-mode-screen");
const workModeMessage = document.getElementById("work-mode-message");
const workModeAvatar = document.getElementById("work-mode-avatar");
const workModeSenderName = document.getElementById("work-mode-sender-name");
const workModeRoomName = document.getElementById("work-mode-room-name");
const workModeCopy = document.getElementById("work-mode-copy");
const workModeTime = document.getElementById("work-mode-time");
const workModeReplyForm = document.getElementById("work-mode-reply-form");
const workModeReplyInput = document.getElementById("work-mode-reply-input");
const workModeFeedback = document.getElementById("work-mode-feedback");
let workModeReadMessageId = "";
let workModeFeedbackTimer = null;
let workModeComposing = false;

function renderWorkModeControl() {
  workModeToggle.setAttribute("aria-pressed", String(state.workModeEnabled));
  workModeToggle.textContent = state.workModeEnabled ? "끄기" : "켜기";
}

function workModeSender(room, message, incomingSender = null) {
  if (incomingSender?.username === message?.username) return incomingSender;
  if (room?.peer?.username === message?.username) return room.peer;
  const participant = room?.participants?.find((candidate) => candidate.username === message?.username);
  if (participant) return participant;
  return state.messenger.friends.find((candidate) => candidate.username === message?.username) || {
    username: message?.username || "",
    display_name: message?.username || "Unknown",
  };
}

function workModeMessageText(message) {
  if (message?.text) return message.text;
  const attachment = message?.attachment;
  if (attachment?.type?.startsWith("image/")) return "사진을 보냈습니다.";
  if (attachment?.type === "application/pdf") return `PDF · ${attachment.name || "파일"}`;
  return attachment?.name || "새 메시지";
}

function renderWorkModeMessage() {
  const item = state.workModeMessage;
  const hasMessage = Boolean(item?.message?.id);
  workModeMessage.classList.toggle("hidden", !hasMessage);
  workModeReplyForm.classList.toggle("hidden", !hasMessage);
  if (!hasMessage) {
    workModeReplyInput.value = "";
    return;
  }
  const { room, message, sender } = item;
  const senderName = sender.display_name || sender.username || message.username;
  workModeAvatar.replaceChildren(createAvatar(
    senderName,
    sender.profile_pixels,
    null,
    sender.status_message,
    sender.profile_thumbnail_url || sender.profile_image_url,
  ));
  workModeSenderName.textContent = senderName;
  workModeRoomName.textContent = room?.name || "채팅";
  workModeCopy.textContent = workModeMessageText(message);
  workModeTime.textContent = formatTime(message.timestamp);
  workModeReplyInput.placeholder = `${senderName}에게 답장 · Enter`;
}

function syncWorkModeVisibility() {
  const visible = Boolean(state.workModeEnabled && state.session?.user);
  document.body.classList.toggle("work-mode-active", visible);
  workModeScreen.classList.toggle("hidden", !visible);
  workModeScreen.setAttribute("aria-hidden", String(!visible));
  renderWorkModeControl();
  if (!visible) return;
  renderWorkModeMessage();
  requestAnimationFrame(() => {
    if (state.workModeMessage) workModeReplyInput.focus({ preventScroll: true });
    else workModeScreen.focus({ preventScroll: true });
  });
}

function setWorkModeEnabled(enabled) {
  state.workModeEnabled = Boolean(enabled);
  localStorage.setItem("colorless-work-mode", state.workModeEnabled ? "on" : "off");
  if (!state.workModeEnabled) {
    state.workModeMessage = null;
    workModeFeedback.textContent = "";
  }
  syncWorkModeVisibility();
}

function toggleWorkMode() {
  if (!state.session?.user) return;
  setWorkModeEnabled(!state.workModeEnabled);
}

function dismissWorkModeMessage() {
  if (!state.workModeMessage) return;
  state.workModeMessage = null;
  workModeReadMessageId = "";
  workModeFeedback.textContent = "";
  renderWorkModeMessage();
  workModeScreen.focus({ preventScroll: true });
}

async function markWorkModeMessageRead() {
  const item = state.workModeMessage;
  if (!state.workModeEnabled || !item?.message?.id || document.hidden) return;
  if (workModeReadMessageId === item.message.id) return;
  workModeReadMessageId = item.message.id;
  try {
    await requestAction("work-mode.mark-read", "/rooms/read", {
      method: "POST",
      body: JSON.stringify({ roomId: item.room.id }),
    });
    const room = state.roomById.get(item.room.id);
    if (room) room.unread_count = 0;
  } catch (_) {
    if (state.workModeMessage?.message?.id === item.message.id) workModeReadMessageId = "";
  }
}

function showWorkModeMessage(room, message, incomingSender = null) {
  if (!state.workModeEnabled || !message?.id) return;
  const currentRoomState = state.roomById.get(room?.id) || room || { id: message.room_id, name: "채팅" };
  state.workModeMessage = {
    room: currentRoomState,
    message,
    sender: workModeSender(currentRoomState, message, incomingSender),
  };
  workModeReadMessageId = "";
  workModeFeedback.textContent = "";
  syncWorkModeVisibility();
  void markWorkModeMessageRead();
}

function showWorkModeFeedback(message) {
  window.clearTimeout(workModeFeedbackTimer);
  workModeFeedback.textContent = message;
  workModeFeedbackTimer = window.setTimeout(() => {
    workModeFeedback.textContent = "";
  }, 1800);
}

async function sendWorkModeReply(event) {
  event.preventDefault();
  if (workModeComposing) return;
  const item = state.workModeMessage;
  const text = workModeReplyInput.value.trim();
  if (!item?.room?.id || !text || state.workModeSending) return;
  state.workModeSending = true;
  workModeReplyInput.disabled = true;
  try {
    const sent = await postChatMessageWithRetry({
      roomId: item.room.id,
      text,
      attachment: null,
      clientMessageId: createClientMessageId(),
    });
    const room = state.roomById.get(item.room.id);
    if (room) {
      room.last_message = sent;
      room.updated_at = sent.timestamp;
    }
    workModeReplyInput.value = "";
    showWorkModeFeedback("전송됨");
  } catch (error) {
    showWorkModeFeedback(error.message);
  } finally {
    state.workModeSending = false;
    workModeReplyInput.disabled = false;
    workModeReplyInput.focus({ preventScroll: true });
  }
}

function handleWorkModeShortcut(event) {
  if (event.altKey && !event.ctrlKey && !event.metaKey && event.code === "KeyW") {
    event.preventDefault();
    toggleWorkMode();
    return;
  }
  if (!state.workModeEnabled || event.key !== "Escape") return;
  event.preventDefault();
  dismissWorkModeMessage();
}

function beginWorkModeComposition() {
  workModeComposing = true;
}

function finishWorkModeComposition() {
  workModeComposing = false;
}
