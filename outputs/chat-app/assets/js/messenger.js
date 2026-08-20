"use strict";

// Room, friend, presence, directory, and realtime synchronization behavior.
function recentChatRooms() {
  return [...state.messenger.rooms].sort((first, second) => {
    const firstHasMessage = Boolean(first.last_message);
    const secondHasMessage = Boolean(second.last_message);
    if (firstHasMessage !== secondHasMessage) return firstHasMessage ? -1 : 1;
    return new Date(second.updated_at).getTime() - new Date(first.updated_at).getTime();
  });
}

function renderChats() {
  chatList.replaceChildren();
  state.roomNodes.clear();
  const context = state.actionBarByTab.chats;
  const query = context.query.trim().toLocaleLowerCase();
  const rooms = recentChatRooms().filter((room) => {
    if (context.filter === "unread" && !(room.unread_count > 0)) return false;
    if (!query) return true;
    const message = room.last_message?.text || "";
    return `${room.name} ${message}`.toLocaleLowerCase().includes(query);
  });
  if (!state.messenger.rooms.length) {
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
      ? (room.last_message.text || (room.last_message.attachment?.type === "application/pdf" ? "PDF" : "Photo"))
      : "Start a conversation.";
    preview.textContent = room.kind === "group" && room.last_message
      ? `${roomParticipantDisplayName(room, room.last_message.username)}: ${lastMessageCopy}`
      : lastMessageCopy;
    copy.append(title, preview);
    const time = document.createElement("time");
    time.className = "item-time";
    time.textContent = formatTime(room.updated_at);
    item.append(createRoomAvatar(room), copy, time);
    chatList.appendChild(item);
    state.roomNodes.set(room.id, item);
  });
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

function removeMessengerRoom(roomId) {
  const previousLength = state.messenger.rooms.length;
  state.messenger.rooms = state.messenger.rooms.filter((room) => room.id !== roomId);
  state.selectedShareRoomIds = state.selectedShareRoomIds.filter((selectedId) => selectedId !== roomId);
  delete state.chatDrafts[roomId];
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
  if (state.activeList === "shorts") {
    for (const username of usernames) patchRoomPresence(username);
  }
}

function schedulePresencePatch(username) {
  if (!username) return;
  state.presencePatchUsernames.add(username);
  if (state.presencePatchFrame !== null) return;
  state.presencePatchFrame = requestAnimationFrame(flushPresencePatches);
}

let realtimeHandlersRegistered = false;

function realtimeViewContext() {
  return { isShortsView: state.activeList === "shorts" };
}

function renderRealtimeLists(isShortsView) {
  if (!isShortsView) renderChats();
  renderShortShareBar();
}

function registerRealtimeHandlers() {
  if (realtimeHandlersRegistered) return;
  realtimeHandlersRegistered = true;

  realtimeEvents.register("hello", () => updatePresence());
  realtimeEvents.register("message_created", async (payload, { isShortsView }) => {
    recordSyncRevision(payload.revision);
    const isIncoming = payload.message?.username !== state.messenger.user?.username;
    if (!isIncoming) return;
    let room;
    appStore.transact("realtime.message-created", () => {
      room = upsertMessengerRoom(payload.room);
      state.lastSeenRoomMessageIds[payload.roomId] = payload.message?.id || "";
      if (payload.roomId === state.selectedRoomId && payload.message?.id) {
        const visibleMessage = addMessageReader(
          payload.message,
          state.messenger.user?.username,
          room,
        );
        if (appendChatMessageState(visibleMessage)) appendChatMessageNode(visibleMessage, true);
      }
    }, { event: payload.type });
    if (payload.roomId === state.selectedRoomId && payload.message?.id) {
      void requestAction("rooms.mark-read", "/rooms/read", {
        method: "POST",
        body: JSON.stringify({ roomId: payload.roomId }),
      }).then(() => {
        if (state.selectedRoomId === payload.roomId) {
          applyMessageReaderToCurrentMessages(state.messenger.user?.username, "messages.mark-read");
        }
      }).catch(() => {});
    } else if (!isShortsView) {
      renderChats();
    }
    if (!state.shortInlineReply && room) showShortMessageNotice(room, payload.message);
    else if (!state.shortInlineReply) renderShortShareBar();
  });
  realtimeEvents.register("room_read", (payload) => {
    recordSyncRevision(payload.revision);
    if (payload.roomId !== state.selectedRoomId) return;
    applyMessageReaderToCurrentMessages(payload.username, "realtime.room-read");
  });
  realtimeEvents.register("room_updated", (payload, { isShortsView }) => {
    recordSyncRevision(payload.revision);
    let room;
    appStore.transact("realtime.room-updated", () => { room = upsertMessengerRoom(payload.room); }, { event: payload.type });
    if (room?.id === state.selectedRoomId) renderChatRoom();
    renderRealtimeLists(isShortsView);
    if (!roomSettingsSheet.classList.contains("hidden")) renderRoomSettings();
  });
  realtimeEvents.register("room_created", (payload, { isShortsView }) => {
    recordSyncRevision(payload.revision);
    let room;
    appStore.transact("realtime.room-created", () => { room = upsertMessengerRoom(payload.room); }, { event: payload.type });
    renderRealtimeLists(isShortsView);
    if (room?.id === state.selectedRoomId) renderChatRoom();
  });
  realtimeEvents.register("friends_updated", async (payload, { isShortsView }) => {
    recordSyncRevision(payload.revision);
    await loadFriendsPage({ reset: true, render: !isShortsView });
    if (isShortsView && !state.shortInlineReply) renderShortShareBar();
  });
  realtimeEvents.register("room_left", (payload, { isShortsView }) => {
    recordSyncRevision(payload.revision);
    appStore.transact("realtime.room-left", () => {
      const selfLeft = payload.username === state.messenger.user?.username;
      if (selfLeft || !payload.room) removeMessengerRoom(payload.roomId);
      else upsertMessengerRoom(payload.room);
    }, { event: payload.type });
    const selfLeft = payload.username === state.messenger.user?.username;
    if ((selfLeft || !payload.room) && payload.roomId === state.selectedRoomId) closeChatRoom();
    if (!selfLeft && payload.room?.id === state.selectedRoomId) {
      renderChatRoom();
      void loadChatMessages({ markRead: false });
    }
    renderRealtimeLists(isShortsView);
  });
  realtimeEvents.register("presence_updated", (payload) => {
    recordSyncRevision(payload.revision);
    let changed = false;
    appStore.transact("realtime.presence-updated", () => { changed = applyPresenceEvent(payload); }, { event: payload.type });
    if (changed) schedulePresencePatch(payload.username);
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
      recordSyncRevision(payload?.revision);
      await Promise.all([
        loadFriendsPage({ reset: true, render: !context.isShortsView }),
        loadRoomsPage({ reset: true, render: !context.isShortsView }),
      ]);
      if (context.isShortsView && !state.shortInlineReply) renderShortShareBar();
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

  state.messenger.friends.forEach((friend) => {
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
