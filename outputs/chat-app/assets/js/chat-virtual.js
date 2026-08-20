"use strict";

// Variable-height message measurements and virtual scroll range calculations.
const CHAT_VIRTUAL_OVERSCAN = typeof CHAT_MESSAGE_VIRTUAL_OVERSCAN_PX === "number" ? CHAT_MESSAGE_VIRTUAL_OVERSCAN_PX : 640;
const CHAT_VIRTUAL_ESTIMATE = typeof CHAT_MESSAGE_ESTIMATED_HEIGHT === "number" ? CHAT_MESSAGE_ESTIMATED_HEIGHT : 72;
const CHAT_VIRTUAL_GAP = typeof CHAT_MESSAGE_ROW_GAP === "number" ? CHAT_MESSAGE_ROW_GAP : 9;

function estimatedChatMessageHeight(message) {
  const text = String(message?.text || "");
  const visualLines = text.split("\n").reduce(
    (total, line) => total + Math.max(1, Math.ceil([...line].length / 28)),
    0,
  );
  const textHeight = Math.min(8, Math.max(1, visualLines)) * 20;
  const attachmentHeight = message?.attachment?.type?.startsWith("image/") ? 190 : (message?.attachment ? 48 : 0);
  return Math.max(
    CHAT_VIRTUAL_ESTIMATE,
    42 + textHeight + attachmentHeight + CHAT_VIRTUAL_GAP,
  );
}

function chatMessageVirtualHeight(message) {
  return state.messageHeights.get(message.id) || estimatedChatMessageHeight(message);
}

function chatVirtualLayout() {
  const offsets = new Array(state.messages.length + 1);
  offsets[0] = 0;
  for (let index = 0; index < state.messages.length; index += 1) {
    offsets[index + 1] = offsets[index] + chatMessageVirtualHeight(state.messages[index]);
  }
  return { offsets, totalHeight: offsets[offsets.length - 1] || 0 };
}

function chatVirtualIndexAtOffset(offsets, target) {
  let low = 0;
  let high = Math.max(0, offsets.length - 1);
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (offsets[middle] < target) low = middle + 1;
    else high = middle;
  }
  return Math.max(0, low - 1);
}

function chatVirtualRange({ scrollToBottom = false } = {}) {
  const { offsets, totalHeight } = chatVirtualLayout();
  if (!state.messages.length) return { start: 0, end: 0, offsets, totalHeight };
  const viewportHeight = Math.max(chatMessageList.clientHeight, 1);
  const scrollTop = scrollToBottom
    ? Math.max(0, totalHeight - viewportHeight)
    : chatMessageList.scrollTop;
  const startOffset = Math.max(0, scrollTop - CHAT_VIRTUAL_OVERSCAN);
  const endOffset = Math.min(totalHeight, scrollTop + viewportHeight + CHAT_VIRTUAL_OVERSCAN);
  const start = chatVirtualIndexAtOffset(offsets, startOffset);
  const end = Math.min(
    state.messages.length,
    chatVirtualIndexAtOffset(offsets, endOffset) + 2,
  );
  return { start, end: Math.max(start + 1, end), offsets, totalHeight };
}

function createChatVirtualSpacer(className, height) {
  const spacer = document.createElement("div");
  spacer.className = `chat-virtual-spacer ${className}`;
  spacer.setAttribute("aria-hidden", "true");
  spacer.style.height = `${Math.max(0, height)}px`;
  return spacer;
}

function refreshChatVirtualSpacers({ preserveAnchor = false } = {}) {
  const topSpacer = chatMessageList.querySelector(".chat-virtual-spacer-top");
  const bottomSpacer = chatMessageList.querySelector(".chat-virtual-spacer-bottom");
  if (!topSpacer || !bottomSpacer) return;
  const { offsets, totalHeight } = chatVirtualLayout();
  const start = Math.max(0, state.renderedMessageStart);
  const end = Math.max(start, state.renderedMessageEnd);
  const previousTopHeight = Number.parseFloat(topSpacer.style.height) || 0;
  const nextTopHeight = offsets[start] || 0;
  topSpacer.style.height = `${nextTopHeight}px`;
  bottomSpacer.style.height = `${Math.max(0, totalHeight - (offsets[end] || totalHeight))}px`;
  if (preserveAnchor && previousTopHeight !== nextTopHeight) {
    chatMessageList.scrollTop += nextTopHeight - previousTopHeight;
  }
}

function measureRenderedChatMessages() {
  let changed = false;
  for (const [messageId, row] of state.messageNodes) {
    if (!row.isConnected) continue;
    const height = Math.ceil(row.getBoundingClientRect().height) + CHAT_VIRTUAL_GAP;
    if (height <= CHAT_VIRTUAL_GAP || state.messageHeights.get(messageId) === height) continue;
    state.messageHeights.set(messageId, height);
    changed = true;
  }
  if (changed) refreshChatVirtualSpacers({ preserveAnchor: true });
  return changed;
}
