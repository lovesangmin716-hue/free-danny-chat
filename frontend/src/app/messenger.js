"use strict";

import { appStore, chatList, chatRoom, chatRoomAvatar, chatRoomPresence, clearChatDraft, createAvatar, formatTime, friendList, getDisplayName, realtimeEvents, requestAction, roomSettingsSheet, setAppStatus, shortShareList, state, userDirectory } from "./core.js";
import { renderRoomSettings } from "./room-settings.js";
import { addMessageReader, appendChatMessageNode, appendChatMessageState, applyMessageReaderToCurrentMessages, applyUpdatedChatMessage, closeChatRoom, loadChatMessages, openChatRoom, openChatRoomAtMessage, removeChatMessageState, renderAllChatMessages, renderChatRoom, scheduleRoomRead, updatePresence } from "./chat.js";
import { dismissWorkModeMessage, showWorkModeMessage, workModeMessage } from "./work-mode.js";
import { renderContextActionBar, renderFriendActionBar, selectFriendForActionBar } from "./action-bar.js";
import { addFriend, loadFriendsPage, loadMessenger, loadRoomsPage, openNewChat, recordSyncRevision, startLiveSync, stopLiveSync, syncLiveState } from "./app.js";
import { ColorlessPlatform } from "./platform/index.js";
import { mergeAuthoritativeRoomSnapshot, mergeRealtimeRoomSnapshot, unreadAfterMessageCreated } from "./platform/room-snapshots.js";
import { applyReactionEvent, canMarkSelectedRoomRead, noteIncomingMessage, updateReplyReferences } from "./message-interactions.js";

// Room, friend, presence, directory, and realtime synchronization behavior.
function ownedIdentityUsernames() {
  return new Set((state.session?.identities || [state.messenger.user]).map((identity) => identity?.username).filter(Boolean));
}

function roomViewerUsername(room) {
  return room?.viewer_identity?.username || state.messenger.user?.username || "";
}

function recentChatRooms() {
  return state.messenger.rooms.filter((room) => (
    ["direct", "group", "ticket_listing", "ticket_deal"].includes(room.kind || "group")
    && state.chatIdentityVisibility[room.viewer_identity_id] !== false
  )).sort((first, second) => {
    const firstHasMessage = Boolean(first.last_message);
    const secondHasMessage = Boolean(second.last_message);
    if (firstHasMessage !== secondHasMessage) return firstHasMessage ? -1 : 1;
    return new Date(second.updated_at).getTime() - new Date(first.updated_at).getTime();
  });
}

function messagePreviewCopy(message, fallback = "첨부 파일") {
  if (message?.text) return message.text;
  if (message?.attachment?.kind === "voice") return `음성 메시지 · ${formatVoiceDuration(message.attachment.duration_ms)}`;
  if (message?.attachment?.type === "application/pdf") return "PDF";
  if (message?.attachment?.type?.startsWith("image/")) return "Photo";
  return fallback;
}

function updateUnreadDocumentTitle() {
  const count = recentChatRooms().reduce((total, room) => total + Math.max(0, Number(room.unread_count) || 0), 0);
  document.title = count ? `(${count > 99 ? "99+" : count}) Colorless` : "Colorless";
}

function renderChats() {
  updateUnreadDocumentTitle();
  chatList.replaceChildren();
  state.roomNodes.clear();
  const context = state.actionBarByTab.chats;
  const query = context.query.trim().toLocaleLowerCase();
  if (query) {
    renderChatSearchResults(query);
    return;
  }
  const rooms = recentChatRooms().filter((room) => {
    if (context.filter === "unread" && !(room.unread_count > 0)) return false;
    return true;
  });
  if (!rooms.length && context.filter !== "unread") {
    const empty = document.createElement("p");
    empty.className = "empty-list";
    empty.textContent = state.isGuest
      ? "로그인하면 채팅을 시작할 수 있어요."
      : "아직 채팅방이 없어요.";
    chatList.appendChild(empty);
    if (!state.isGuest) {
      const newChatButton = document.createElement("button");
      newChatButton.type = "button";
      newChatButton.className = "secondary-button empty-list-action";
      newChatButton.textContent = "새 채팅";
      ColorlessPlatform.decorateIconButton(newChatButton, "message-plus", { label: "새 채팅", visibleLabel: true });
      newChatButton.addEventListener("click", openNewChat);
      chatList.appendChild(newChatButton);
    }
    return;
  }

  if (!rooms.length) {
    const empty = document.createElement("p");
    empty.className = "empty-list";
    empty.textContent = query ? "검색 결과가 없어요." : "안 읽은 채팅이 없어요.";
    chatList.appendChild(empty);
    return;
  }

  rooms.forEach((room) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "list-item";
    item.dataset.roomId = room.id;
    if (room.peer?.username) item.dataset.peerUsername = room.peer.username;
    item.addEventListener("click", () => openChatRoom(room.id));

    const copy = document.createElement("div");
    copy.className = "item-copy";
    const title = document.createElement("strong");
    title.className = "item-title";
    title.textContent = room.name;
    /* const preview = document.createElement("span");
    preview.className = "item-preview"; /*
    /*
    preview.textContent = room.last_message ? room.last_message.text : "대화를 시작해 보세요.";
    */ /*
    preview.textContent = room.last_message ? room.last_message.text : "\ub300\ud654\ub97c \uc2dc\uc791\ud574 \ubcf4\uc138\uc694.";
    */ /* preview.textContent = friend.presence?.online ? "online" : (friend.status_message || "offline");
    copy.append(title, preview);

    */ const preview = document.createElement("span");
    preview.className = "item-preview";
    const lastMessageCopy = room.last_message
      ? messagePreviewCopy(room.last_message)
      : "Start a conversation.";
    preview.textContent = room.kind === "group" && room.last_message
      ? `${roomParticipantDisplayName(room, room.last_message.username)}: ${lastMessageCopy}`
      : lastMessageCopy;
    copy.append(title, preview);
    const time = document.createElement("time");
    time.className = "item-time";
    time.textContent = formatTime(room.updated_at);
    const meta = document.createElement("span");
    meta.className = "item-meta";
    meta.appendChild(time);
    const unreadCount = Math.max(0, Number(room.unread_count) || 0);
    if (unreadCount) {
      const unread = document.createElement("span");
      unread.className = "item-unread";
      unread.textContent = unreadCount > 99 ? "99+" : String(unreadCount);
      unread.setAttribute("aria-label", `안 읽은 메시지 ${unreadCount}개`);
      meta.appendChild(unread);
    }
    item.setAttribute("aria-label", unreadCount ? `${room.name}, 안 읽은 메시지 ${unreadCount}개` : room.name);
    item.append(createRoomAvatar(room), copy, meta);
    chatList.appendChild(item);
    state.roomNodes.set(room.id, item);
  });
}

function resetChatSearch() {
  window.clearTimeout(state.chatSearchTimer);
  state.chatSearchTimer = null;
  state.chatSearchRequestId += 1;
  state.chatSearchLoading = false;
  state.chatSearchResults = [];
}

function scheduleChatSearch(value) {
  window.clearTimeout(state.chatSearchTimer);
  state.chatSearchTimer = null;
  const query = value.trim();
  state.chatSearchRequestId += 1;
  const requestId = state.chatSearchRequestId;
  state.chatSearchResults = [];
  state.chatSearchLoading = Boolean(query);
  renderChats();
  if (!query) return;
  state.chatSearchTimer = window.setTimeout(async () => {
    try {
      const payload = await requestAction(
        "messages.search",
        `/messages/search?q=${encodeURIComponent(query)}&limit=50`,
        {},
        { key: `messages.search:${query}`, policy: "replace" },
      );
      if (requestId !== state.chatSearchRequestId || state.actionBarByTab.chats.query.trim() !== query) return;
      state.chatSearchResults = payload.items || [];
    } catch (error) {
      if (requestId !== state.chatSearchRequestId) return;
      state.chatSearchResults = [];
      setAppStatus(error.message, "error");
    } finally {
      if (requestId === state.chatSearchRequestId) {
        state.chatSearchLoading = false;
        renderChats();
      }
    }
  }, 220);
}

function renderChatSearchResults(query) {
  const visibleResults = state.chatSearchResults.filter(
    (result) => state.chatIdentityVisibility[result.room?.viewer_identity_id] !== false,
  );
  if (state.chatSearchLoading && !visibleResults.length) {
    const loading = document.createElement("p");
    loading.className = "empty-list";
    loading.textContent = "대화를 검색하는 중이에요.";
    chatList.appendChild(loading);
    return;
  }
  if (!visibleResults.length) {
    const empty = document.createElement("p");
    empty.className = "empty-list";
    empty.textContent = `“${query}” 검색 결과가 없어요.`;
    chatList.appendChild(empty);
    return;
  }
  for (const result of visibleResults) {
    const room = result.room;
    if (!room?.id) continue;
    const item = document.createElement("button");
    item.type = "button";
    item.className = "list-item chat-search-result";
    item.dataset.roomId = room.id;
    const copy = document.createElement("div");
    copy.className = "item-copy";
    const title = document.createElement("strong");
    title.className = "item-title";
    title.textContent = room.name;
    const preview = document.createElement("span");
    preview.className = "item-preview";
    if (result.kind === "message" && result.message) {
      const sender = roomParticipantDisplayName(room, result.message.username);
      preview.textContent = `${sender}: ${messagePreviewCopy(result.message)}`;
      item.addEventListener("click", () => openChatRoomAtMessage(room, result.message.id));
    } else {
      preview.textContent = "대화방 열기";
      item.addEventListener("click", () => openChatRoomAtMessage(room, ""));
    }
    const time = document.createElement("time");
    time.className = "item-time";
    time.textContent = result.message ? formatTime(result.message.timestamp) : "";
    item.append(createRoomAvatar(room), copy, time);
    copy.append(title, preview);
    chatList.appendChild(item);
  }
}

function currentRoom() {
  return state.messenger.rooms.find((room) => room.id === state.selectedRoomId) || null;
}

function roomParticipantDisplayName(room, username) {
  const participant = room?.participants?.find((candidate) => candidate.username === username);
  return participant?.display_name || participant?.username || username || "Unknown";
}

function createRoomAvatar(room) {
  if (room?.kind === "group") {
    return createAvatar(room.name, [], null, "", room.image_thumbnail_url || room.image_url);
  }
  return createAvatar(
    room.name,
    room.peer?.profile_pixels,
    room.peer?.presence,
    room.peer?.status_message,
    room.peer?.profile_thumbnail_url || room.peer?.profile_image_url,
  );
}

function mergeRoomPeer(room) {
  if (!room?.peer?.id) return room;
  const friend = state.messenger.friends.find((candidate) => candidate.id === room.peer.id);
  return friend ? { ...room, peer: { ...friend, ...room.peer } } : room;
}

function rebuildPresenceIndexes() {
  state.friendByUsername.clear();
  state.roomById.clear();
  state.roomIdsByPeerUsername.clear();
  for (const friend of state.messenger.friends) state.friendByUsername.set(friend.username, friend);
  for (const room of state.messenger.rooms) {
    state.roomById.set(room.id, room);
    const username = room.peer?.username;
    if (!username) continue;
    const roomIds = state.roomIdsByPeerUsername.get(username) || new Set();
    roomIds.add(room.id);
    state.roomIdsByPeerUsername.set(username, roomIds);
  }
}

function upsertMessengerRoom(incomingRoom) {
  if (!incomingRoom?.id) return null;
  const index = state.messenger.rooms.findIndex((room) => room.id === incomingRoom.id);
  const existing = index >= 0 ? state.messenger.rooms[index] : {};
  const room = mergeRoomPeer({ ...existing, ...incomingRoom });
  if (index >= 0) state.messenger.rooms[index] = room;
  else state.messenger.rooms.push(room);
  state.messenger.rooms.sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)));
  rebuildPresenceIndexes();
  return room;
}

function upsertRoomAfterMessageDeletion(incomingRoom) {
  if (!incomingRoom?.id) return null;
  const existing = state.roomById.get(incomingRoom.id);
  return upsertMessengerRoom({
    ...incomingRoom,
    name: existing?.name || incomingRoom.name,
    peer: existing?.peer || incomingRoom.peer,
    unread_count: existing?.unread_count ?? incomingRoom.unread_count ?? 0,
  });
}

function removeMessengerRoom(roomId) {
  const removedRoom = state.roomById.get(roomId);
  clearChatDraft(roomId, removedRoom?.viewer_identity_id || removedRoom?.viewer_identity?.id || "");
  for (const message of state.chatOutbox.clearRoom(roomId)) {
    if (message.retry_data) message.retry_data.cancelled = true;
    if (message.preview_url) URL.revokeObjectURL(message.preview_url);
  }
  const previousLength = state.messenger.rooms.length;
  state.messenger.rooms = state.messenger.rooms.filter((room) => room.id !== roomId);
  state.selectedShareRoomIds = state.selectedShareRoomIds.filter((selectedId) => selectedId !== roomId);
  delete state.lastSeenRoomMessageIds[roomId];
  rebuildPresenceIndexes();
  return state.messenger.rooms.length !== previousLength;
}

function applyPresenceEvent(payload) {
  if (!payload.username || !payload.presence) return false;
  let changed = false;
  const friend = state.friendByUsername.get(payload.username);
  if (friend) {
    friend.presence = payload.presence;
    changed = true;
  }
  for (const roomId of state.roomIdsByPeerUsername.get(payload.username) || []) {
    const room = state.roomById.get(roomId);
    if (!room?.peer) continue;
    room.peer.presence = payload.presence;
    changed = true;
  }
  return changed;
}

function replacePresenceAvatar(container, avatar) {
  const currentAvatar = container?.querySelector(".avatar-wrap");
  if (currentAvatar) currentAvatar.replaceWith(avatar);
}

function patchFriendPresence(username) {
  const friend = state.friendByUsername.get(username);
  const row = state.friendNodes.get(username);
  if (!friend || !row?.isConnected) return;
  replacePresenceAvatar(row, createAvatar(
    getDisplayName(friend),
    friend.profile_pixels,
    friend.presence,
    friend.status_message,
    friend.profile_thumbnail_url || friend.profile_image_url,
  ));
  const preview = row.querySelector(".item-preview");
  if (preview) preview.textContent = friend.presence?.online ? "online" : (friend.status_message || "offline");
}

function patchRoomPresence(username) {
  for (const roomId of state.roomIdsByPeerUsername.get(username) || []) {
    const room = state.roomById.get(roomId);
    if (!room) continue;
    const row = state.roomNodes.get(room.id);
    if (row?.isConnected) replacePresenceAvatar(row, createRoomAvatar(room));
    shortShareList.querySelectorAll(".short-share-person").forEach((person) => {
      if (person.dataset.roomId === room.id) replacePresenceAvatar(person, createRoomAvatar(room));
    });
    if (room.id === state.selectedRoomId && !chatRoom.classList.contains("hidden")) {
      const presence = room.peer?.presence;
      const isInThisRoom = Boolean(presence?.online && presence.active_room_ids?.includes(room.id));
      chatRoomAvatar.replaceChildren(createRoomAvatar(room));
      chatRoomPresence.textContent = isInThisRoom ? "in chat" : (presence?.online ? "online" : "");
    }
  }
}

function flushPresencePatches() {
  state.presencePatchFrame = null;
  const usernames = [...state.presencePatchUsernames];
  state.presencePatchUsernames.clear();
  if (state.activeList === "friends") {
    for (const username of usernames) patchFriendPresence(username);
    renderFriendActionBar();
    return;
  }
  if (state.activeList === "chats") {
    for (const username of usernames) patchRoomPresence(username);
    return;
  }
}

function schedulePresencePatch(username) {
  if (!username) return;
  state.presencePatchUsernames.add(username);
  if (state.presencePatchFrame !== null) return;
  state.presencePatchFrame = requestAnimationFrame(flushPresencePatches);
}

let realtimeHandlersRegistered = false;

function numericMutationRevision(value) {
  const revision = Number(value);
  return Number.isSafeInteger(revision) && revision >= 0 ? revision : null;
}

function messageEventRevision(payload) {
  return numericMutationRevision(payload?.message?.mutation_revision
    ?? payload?.mutationRevision
    ?? payload?.mutation_revision);
}

function personalizeMessageSnapshot(message, viewerUsername, previous = null) {
  if (!message?.id || message.reactions === undefined) return message;
  const reactedByMe = new Map((previous?.reactions || []).map(
    (reaction) => [reaction.emoji, Boolean(reaction.reacted_by_me)],
  ));
  return {
    ...message,
    reactions: (message.reactions || []).map((reaction) => ({
      ...reaction,
      reacted_by_me: reaction.reacted_by_me
        ?? (Array.isArray(reaction.usernames)
          ? reaction.usernames.includes(viewerUsername)
          : (reactedByMe.get(reaction.emoji) ?? false)),
    })),
  };
}

function reactionMessageSnapshot(current, payload, viewerUsername) {
  if (!current) return null;
  const incomingRevision = messageEventRevision(payload);
  const currentRevision = numericMutationRevision(current.mutation_revision);
  if (incomingRevision !== null && currentRevision !== null && incomingRevision < currentRevision) return current;
  const reactedByMe = new Map((current.reactions || []).map(
    (reaction) => [reaction.emoji, Boolean(reaction.reacted_by_me)],
  ));
  const snapshot = payload.message?.id ? {
    ...current,
    ...payload.message,
    read_by: payload.message.read_by ?? current.read_by,
    unread_by: payload.message.unread_by ?? current.unread_by,
    reactions: (payload.message.reactions || []).map((reaction) => ({
      ...reaction,
      reacted_by_me: reaction.reacted_by_me
        ?? (Array.isArray(reaction.usernames)
          ? reaction.usernames.includes(viewerUsername)
          : (reactedByMe.get(reaction.emoji) ?? false)),
    })),
  } : current;
  return applyReactionEvent(snapshot, payload, viewerUsername);
}

function scheduleMessageReconciliation(roomId, messageId, currentRevision, incomingRevision, force = false) {
  if (
    roomId !== state.selectedRoomId
    || (!force && (currentRevision === null
      || incomingRevision === null
      || incomingRevision <= currentRevision + 1))
  ) return;
  const key = `${roomId}:${messageId}`;
  if (state.messageReconciliationTimers.has(key)) return;
  const authEpoch = state.authEpoch;
  state.messageReconciliationTimers.set(key, window.setTimeout(async () => {
    state.messageReconciliationTimers.delete(key);
    if (state.authEpoch !== authEpoch) return;
    try {
      const payload = await requestAction(
        "messages.reconcile",
        `/messages?room_id=${encodeURIComponent(roomId)}&limit=3&around=${encodeURIComponent(messageId)}`,
        {},
        { key: `messages.reconcile:${key}`, policy: "replace" },
      );
      if (state.authEpoch !== authEpoch || roomId !== state.selectedRoomId) return;
      const message = (Array.isArray(payload) ? payload : (payload.items || []))
        .find((candidate) => candidate.id === messageId);
      if (message) {
        let record = state.messageEventJournal.snapshot(roomId, message, message.mutation_revision);
        if (!record?.deleted && !applyUpdatedChatMessage(messageId, record.message)) {
          const lastTimestamp = Date.parse(state.messages.at(-1)?.timestamp);
          const messageTimestamp = Date.parse(record.message.timestamp);
          if (!Number.isFinite(lastTimestamp) || messageTimestamp >= lastTimestamp) {
            record = state.messageEventJournal.snapshot(roomId, record.message, record.revision, true);
            if (appendChatMessageState(record.message)) appendChatMessageNode(record.message, false, true);
          }
        }
      }
    } catch (_) {
    }
  }, 80));
}

function realtimeViewContext() { return {}; }

function renderRealtimeLists() {
  renderChats();
  renderContextActionBar();
}

function registerRealtimeHandlers() {
  if (realtimeHandlersRegistered) return;
  realtimeHandlersRegistered = true;

  realtimeEvents.register("hello", () => updatePresence());
  realtimeEvents.register("sync_required", async (payload) => {
    const authEpoch = state.authEpoch;
    await loadMessenger();
    if (state.authEpoch !== authEpoch) return;
    recordSyncRevision(payload.revision);
  });
  realtimeEvents.register("message_created", async (payload) => {
    const createdMessage = personalizeMessageSnapshot(
      payload.message,
      roomViewerUsername(state.roomById.get(payload.roomId) || payload.room),
    );
    const resolved = state.messageEventJournal.resolve(payload.roomId, createdMessage);
    if (resolved.deleted) {
      recordSyncRevision(payload.revision);
      return;
    }
    const eventMessage = resolved.message;
    state.messageEventJournal.snapshot(payload.roomId, eventMessage, messageEventRevision(payload), true);
    const eventRoom = payload.room?.last_message?.id === eventMessage?.id
      ? { ...payload.room, last_message: eventMessage }
      : payload.room;
    const isIncoming = !ownedIdentityUsernames().has(eventMessage?.username);
    const isTicketRoom = ["ticket_listing", "ticket_deal"].includes(eventRoom?.kind);
    const isSelected = payload.roomId === state.selectedRoomId;
    const autoScroll = isSelected && canMarkSelectedRoomRead(payload.roomId);
    let room;
    let messageAdded = false;
    appStore.transact("realtime.message-created", () => {
      const existingRoom = state.roomById.get(payload.roomId);
      const previousLastMessageId = existingRoom?.last_message?.id || "";
      room = upsertMessengerRoom(mergeRealtimeRoomSnapshot(existingRoom, eventRoom));
      state.lastSeenRoomMessageIds[payload.roomId] = room?.last_message?.id || eventMessage?.id || "";
      if (isSelected && eventMessage?.id) {
        const visibleMessage = isIncoming && autoScroll
          ? addMessageReader(eventMessage, roomViewerUsername(room), room)
          : eventMessage;
        const pending = state.messages.find((message) => (
          (message.pending || message.failed) && message.client_message_id === visibleMessage.client_message_id
        ));
        if (pending) applyUpdatedChatMessage(pending.id, visibleMessage);
        else {
          messageAdded = appendChatMessageState(visibleMessage);
          if (messageAdded) appendChatMessageNode(visibleMessage, autoScroll, !autoScroll);
        }
      } else if (eventMessage?.id) {
        messageAdded = previousLastMessageId !== eventMessage.id;
      }
      room.unread_count = unreadAfterMessageCreated(
        existingRoom, { isIncoming, messageAdded, autoRead: autoScroll },
      );
      if (isIncoming && messageAdded && autoScroll) room._local_unread_known = true;
    }, { event: payload.type });
    if (isIncoming && messageAdded && !isTicketRoom) showWorkModeMessage(room, eventMessage, payload.sender);
    if (isIncoming && isSelected && messageAdded) noteIncomingMessage(autoScroll);
    if (isIncoming && autoScroll && messageAdded) {
      if (room) room.unread_count = 0;
      scheduleRoomRead(payload.roomId);
    }
    renderChats();
    renderContextActionBar();
    recordSyncRevision(payload.revision);
  });
  realtimeEvents.register("message_updated", (payload) => {
    const messageId = payload.messageId || payload.message?.id;
    const updatedMessage = personalizeMessageSnapshot(
      payload.message,
      roomViewerUsername(state.roomById.get(payload.roomId)),
    );
    const record = updatedMessage?.id
      ? state.messageEventJournal.snapshot(payload.roomId, updatedMessage, messageEventRevision(payload))
      : null;
    if (record?.deleted) {
      recordSyncRevision(payload.revision);
      return;
    }
    const eventMessage = record?.message || payload.message;
    if (payload.roomId === state.selectedRoomId && eventMessage?.id) {
      const index = state.messageIndexes.get(messageId);
      const current = Number.isInteger(index) ? state.messages[index] : null;
      scheduleMessageReconciliation(
        payload.roomId,
        messageId,
        numericMutationRevision(current?.mutation_revision),
        messageEventRevision(payload),
      );
      applyUpdatedChatMessage(messageId, eventMessage);
      // The source can be outside the loaded window while one of its replies
      // is visible, so update reply snapshots independently of source lookup.
      if (!current) updateReplyReferences(messageId, eventMessage);
    } else if (payload.roomId === state.selectedRoomId) {
      scheduleMessageReconciliation(payload.roomId, messageId, null, messageEventRevision(payload), true);
    }
    const room = state.roomById.get(payload.roomId);
    if (room?.last_message?.id === messageId) {
      const currentRevision = Number(room.last_message.mutation_revision);
      const incomingRevision = Number(eventMessage?.mutation_revision);
      if (!(
        Number.isSafeInteger(currentRevision)
        && Number.isSafeInteger(incomingRevision)
        && incomingRevision < currentRevision
      )) room.last_message = { ...room.last_message, ...eventMessage };
    }
    renderChats();
    recordSyncRevision(payload.revision);
  });
  realtimeEvents.register("message_reaction_updated", (payload) => {
    let eventMessage = payload.message?.id
      ? reactionMessageSnapshot(payload.message, payload, roomViewerUsername(state.roomById.get(payload.roomId)))
      : null;
    const record = eventMessage
      ? state.messageEventJournal.snapshot(payload.roomId, eventMessage, messageEventRevision(payload))
      : null;
    if (record?.deleted) {
      recordSyncRevision(payload.revision);
      return;
    }
    eventMessage = record?.message || eventMessage;
    if (payload.roomId === state.selectedRoomId) {
      const index = state.messageIndexes.get(payload.messageId);
      const message = Number.isInteger(index) ? state.messages[index] : null;
      if (message) {
        scheduleMessageReconciliation(
          payload.roomId,
          payload.messageId,
          numericMutationRevision(message.mutation_revision),
          messageEventRevision(payload),
        );
        applyUpdatedChatMessage(message.id, eventMessage
          || reactionMessageSnapshot(message, payload, roomViewerUsername(state.roomById.get(payload.roomId))));
      } else if (!eventMessage) scheduleMessageReconciliation(
        payload.roomId, payload.messageId, null, messageEventRevision(payload), true,
      );
    }
    const room = state.roomById.get(payload.roomId);
    if (room?.last_message?.id === payload.messageId) {
      room.last_message = eventMessage || reactionMessageSnapshot(room.last_message, payload, roomViewerUsername(room));
    }
    renderChats();
    recordSyncRevision(payload.revision);
  });
  realtimeEvents.register("message_deleted", async (payload) => {
    const authEpoch = state.authEpoch;
    state.messageEventJournal.tombstone(
      payload.roomId, payload.messageId, messageEventRevision(payload), payload.room,
    );
    const reconciliationKey = `${payload.roomId}:${payload.messageId}`;
    window.clearTimeout(state.messageReconciliationTimers.get(reconciliationKey));
    state.messageReconciliationTimers.delete(reconciliationKey);
    appStore.transact("realtime.message-deleted", () => {
      if (payload.room) {
        upsertRoomAfterMessageDeletion(mergeRealtimeRoomSnapshot(
          state.roomById.get(payload.roomId), payload.room,
        ));
      }
      const latestMessageId = payload.room?.last_message?.id || "";
      state.lastSeenRoomMessageIds[payload.roomId] = latestMessageId;
      if (payload.roomId === state.selectedRoomId && payload.messageId) {
        removeChatMessageState(payload.messageId);
        updateReplyReferences(payload.messageId);
      }
    }, { event: payload.type });
    if (state.workModeMessage?.message?.id === payload.messageId) dismissWorkModeMessage();
    if (payload.roomId === state.selectedRoomId) renderAllChatMessages();
    try {
      await loadRoomsPage({ reset: true, render: false });
    } catch (_) {
    }
    if (state.authEpoch !== authEpoch) return;
    renderRealtimeLists();
    recordSyncRevision(payload.revision);
  });
  realtimeEvents.register("room_read", (payload) => {
    const room = state.roomById.get(payload.roomId);
    if (
      room?.last_message?.id === payload.lastReadMessageId
      && payload.username === roomViewerUsername(room)
    ) {
      room.unread_count = 0;
      room._local_unread_known = true;
      renderChats();
    }
    if (payload.roomId === state.selectedRoomId && payload.lastReadMessageId) {
      applyMessageReaderToCurrentMessages(payload.username, "realtime.room-read", payload.lastReadMessageId);
    }
    recordSyncRevision(payload.revision);
  });
  realtimeEvents.register("room_updated", (payload) => {
    let room;
    appStore.transact("realtime.room-updated", () => {
      room = upsertMessengerRoom(mergeRealtimeRoomSnapshot(
        state.roomById.get(payload.room?.id), payload.room,
      ));
    }, { event: payload.type });
    if (room?.id === state.selectedRoomId) renderChatRoom();
    renderRealtimeLists();
    if (!roomSettingsSheet.classList.contains("hidden")) renderRoomSettings();
    recordSyncRevision(payload.revision);
  });
  realtimeEvents.register("room_created", (payload) => {
    let room;
    appStore.transact("realtime.room-created", () => {
      room = upsertMessengerRoom(mergeAuthoritativeRoomSnapshot(
        state.roomById.get(payload.room?.id), payload.room,
      ));
    }, { event: payload.type });
    renderRealtimeLists();
    if (room?.id === state.selectedRoomId) renderChatRoom();
    recordSyncRevision(payload.revision);
  });
  realtimeEvents.register("friends_updated", async (payload) => {
    const authEpoch = state.authEpoch;
    await loadFriendsPage({ reset: true, render: true });
    if (state.authEpoch !== authEpoch) return;
    recordSyncRevision(payload.revision);
  });
  realtimeEvents.register("room_left", (payload) => {
    appStore.transact("realtime.room-left", () => {
      const selfLeft = ownedIdentityUsernames().has(payload.username);
      if (selfLeft || !payload.room) removeMessengerRoom(payload.roomId);
      else upsertMessengerRoom(mergeRealtimeRoomSnapshot(
        state.roomById.get(payload.roomId), payload.room,
      ));
    }, { event: payload.type });
    const selfLeft = ownedIdentityUsernames().has(payload.username);
    if ((selfLeft || !payload.room) && payload.roomId === state.selectedRoomId) closeChatRoom();
    if (!selfLeft && payload.room?.id === state.selectedRoomId) {
      renderChatRoom();
      void loadChatMessages({ markRead: false });
    }
    renderRealtimeLists();
    recordSyncRevision(payload.revision);
  });
  realtimeEvents.register("presence_updated", (payload) => {
    let changed = false;
    appStore.transact("realtime.presence-updated", () => { changed = applyPresenceEvent(payload); }, { event: payload.type });
    if (changed) schedulePresencePatch(payload.username);
    recordSyncRevision(payload.revision);
  });
}

function connectEvents() {
  if (state.eventSource || !state.session?.user) return;
  registerRealtimeHandlers();
  const source = ColorlessPlatform.createRealtimeClient({
    url: "/events",
    router: realtimeEvents,
    context: realtimeViewContext,
    onUnhandled: async (payload, context) => {
      const authEpoch = state.authEpoch;
      await Promise.all([
        loadFriendsPage({ reset: true, render: true }),
        loadRoomsPage({ reset: true, render: true }),
      ]);
      if (state.authEpoch !== authEpoch) return;
      recordSyncRevision(payload?.revision);
    },
    onOpen: () => {
      if (state.eventSource !== source) return;
      const isReconnect = state.eventEverConnected;
      state.eventEverConnected = true;
      state.eventConnected = true;
      stopLiveSync();
      if (isReconnect) void syncLiveState();
    },
    onError: () => {
      source.close();
      if (state.eventSource === source) {
        state.eventSource = null;
        state.eventConnected = false;
        startLiveSync();
        window.clearTimeout(state.eventReconnectTimer);
        state.eventReconnectTimer = window.setTimeout(connectEvents, 1200);
      }
    },
  });
  state.eventSource = source;
  source.open();
}

function renderFriends() {
  friendList.replaceChildren();
  state.friendNodes.clear();
  if (!state.messenger.friends.length) {
    const empty = document.createElement("p");
    empty.className = "empty-list";
    empty.textContent = state.isGuest
      ? "로그인하면 친구를 추가할 수 있어요."
      : "친구가 없어요. 오른쪽 위 친구 추가 버튼으로 사용자를 추가해 보세요.";
    friendList.appendChild(empty);
    return;
  }

  const query = state.actionBarByTab.friends.query.trim().toLocaleLowerCase();
  const matchingFriends = state.messenger.friends.filter((friend) => !query || [
    getDisplayName(friend),
    friend.username,
    friend.friend_code,
  ].some((value) => String(value || "").toLocaleLowerCase().includes(query)));
  if (!matchingFriends.length) {
    const empty = document.createElement("p");
    empty.className = "empty-list";
    empty.textContent = "검색한 친구가 없어요.";
    friendList.appendChild(empty);
    return;
  }

  matchingFriends.forEach((friend) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "list-item";
    item.dataset.username = friend.username;
    item.setAttribute("aria-label", `${getDisplayName(friend)} 프로필 보기`);
    item.addEventListener("click", () => selectFriendForActionBar(friend.id));
    const copy = document.createElement("div");
    copy.className = "item-copy";
    const title = document.createElement("strong");
    title.className = "item-title";
    title.textContent = getDisplayName(friend);
    /*
    /* const preview = document.createElement("span");
    preview.className = "item-preview";
    preview.textContent = friend.status_message || "접속 중";
    */ /*
    const preview = document.createElement("span");
    preview.className = "item-preview";
    preview.textContent = friend.presence?.online ? "활동 중" : (friend.status_message || "오프라인");
    copy.append(title, preview);

    */ const preview = document.createElement("span");
    preview.className = "item-preview";
    preview.textContent = friend.presence?.online ? "online" : (friend.status_message || "offline");
    copy.append(title, preview);
    /* const startButton = document.createElement("button");
    startButton.type = "button";
    startButton.className = "friend-action";
    startButton.textContent = "대화 시작";
    startButton.addEventListener("click", () => createDirectRoom(friend.id));
    item.append(createAvatar(getDisplayName(friend), friend.profile_pixels, friend.presence), copy, startButton); */
    item.append(createAvatar(getDisplayName(friend), friend.profile_pixels, friend.presence, friend.status_message, friend.profile_thumbnail_url || friend.profile_image_url), copy);
    friendList.appendChild(item);
    state.friendNodes.set(friend.username, item);
  });
}

function renderDirectory() {
  userDirectory.replaceChildren();
  if (!state.messenger.discoverableUsers.length) {
    const empty = document.createElement("p");
    empty.className = "empty-list";
    empty.textContent = "친구 ID를 입력해 추가하세요.";
    userDirectory.appendChild(empty);
    return;
  }

  state.messenger.discoverableUsers.forEach((user) => {
    const item = document.createElement("div");
    item.className = "directory-item";
    const copy = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = getDisplayName(user);
    const status = document.createElement("span");
    /*
    status.textContent = user.status_message || "접속 중";
    */
    status.textContent = user.status_message || "\uc628\ub77c\uc778";
    copy.append(name, status);

    const addButton = document.createElement("button");
    addButton.type = "button";
    addButton.className = "friend-action";
    addButton.textContent = "친구 추가";
    addButton.addEventListener("click", () => addFriend(user.friend_code || user.friendCode || ""));
    item.append(createAvatar(getDisplayName(user), user.profile_pixels, null, "", user.profile_thumbnail_url || user.profile_image_url), copy, addButton);
    userDirectory.appendChild(item);
  });
}

export {
  connectEvents,
  createRoomAvatar,
  currentRoom,
  rebuildPresenceIndexes,
  recentChatRooms,
  registerRealtimeHandlers,
  removeMessengerRoom,
  renderChats,
  renderDirectory,
  renderFriends,
  resetChatSearch,
  roomParticipantDisplayName,
  scheduleChatSearch,
  upsertMessengerRoom,
  upsertRoomAfterMessageDeletion,
};
