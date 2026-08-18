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
      newChatButton.addEventListener("click", openNewChat);
      chatList.appendChild(newChatButton);
    }
    return;
  }

  recentChatRooms().forEach((room) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "list-item";
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

function upsertMessengerRoom(incomingRoom) {
  if (!incomingRoom?.id) return null;
  const index = state.messenger.rooms.findIndex((room) => room.id === incomingRoom.id);
  const existing = index >= 0 ? state.messenger.rooms[index] : {};
  const room = mergeRoomPeer({ ...existing, ...incomingRoom });
  if (index >= 0) state.messenger.rooms[index] = room;
  else state.messenger.rooms.push(room);
  state.messenger.rooms.sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)));
  return room;
}

function removeMessengerRoom(roomId) {
  const previousLength = state.messenger.rooms.length;
  state.messenger.rooms = state.messenger.rooms.filter((room) => room.id !== roomId);
  if (state.selectedShareRoomId === roomId) state.selectedShareRoomId = "";
  delete state.chatDrafts[roomId];
  delete state.lastSeenRoomMessageIds[roomId];
  return state.messenger.rooms.length !== previousLength;
}

function applyPresenceEvent(payload) {
  if (!payload.username || !payload.presence) return;
  state.messenger.friends = state.messenger.friends.map((friend) => (
    friend.username === payload.username ? { ...friend, presence: payload.presence } : friend
  ));
  state.messenger.rooms = state.messenger.rooms.map((room) => (
    room.peer?.username === payload.username
      ? { ...room, peer: { ...room.peer, presence: payload.presence } }
      : room
  ));
}

function connectEvents() {
  if (state.eventSource || !state.session?.user) return;
  const source = new EventSource("/events");
  state.eventSource = source;
  source.onopen = () => {
    if (state.eventSource !== source) return;
    const isReconnect = state.eventEverConnected;
    state.eventEverConnected = true;
    state.eventConnected = true;
    stopLiveSync();
    if (isReconnect) void syncLiveState();
  };
  source.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    if (payload.type === "hello") {
      updatePresence();
      return;
    }
    const isShortsView = state.activeList === "shorts";
    if (payload.type === "message_created") {
      const isIncoming = payload.message?.username !== state.messenger.user?.username;
      if (!isIncoming) return;
      const room = upsertMessengerRoom(payload.room);
      state.lastSeenRoomMessageIds[payload.roomId] = payload.message?.id || "";
      if (payload.roomId === state.selectedRoomId && payload.message?.id) {
        if (appendChatMessageState(payload.message)) {
          appendChatMessageNode(payload.message, true);
        }
        void api("/rooms/read", {
          method: "POST",
          body: JSON.stringify({ roomId: payload.roomId }),
        }).catch(() => {});
      } else if (!isShortsView) {
        renderChats();
      }
      if (isShortsView) {
        if (!state.shortInlineReply && isIncoming && room) showShortMessageNotice(room, payload.message);
        else if (!state.shortInlineReply) renderShortShareBar();
      }
      return;
    }
    if (payload.type === "room_read") {
      if (payload.username !== state.messenger.user?.username && payload.roomId === state.selectedRoomId) {
        if (currentRoom()?.kind === "group") {
          void loadChatMessages({ markRead: false });
          return;
        }
        const changedMessages = [];
        for (let index = 0; index < state.messages.length; index += 1) {
          const message = state.messages[index];
          if (message.username !== state.messenger.user?.username || message.read) continue;
          const readMessage = { ...message, read: true };
          state.messages[index] = readMessage;
          changedMessages.push([message.id, readMessage]);
        }
        if (changedMessages.length) {
          state.messageRevision += 1;
          for (const [messageId, message] of changedMessages) {
            replaceChatMessageNode(messageId, message);
          }
        }
      }
      return;
    }
    if (payload.type === "room_updated") {
      const room = upsertMessengerRoom(payload.room);
      if (room?.id === state.selectedRoomId) renderChatRoom();
      if (isShortsView) renderShortShareBar();
      else renderChats();
      if (!roomSettingsSheet.classList.contains("hidden")) renderRoomSettings();
      return;
    }
    if (payload.type === "room_created") {
      const room = upsertMessengerRoom(payload.room);
      if (isShortsView) renderShortShareBar();
      else renderChats();
      if (room?.id === state.selectedRoomId) renderChatRoom();
      return;
    }
    if (payload.type === "friends_updated") {
      void loadMessenger(!isShortsView).then(() => {
        if (isShortsView && !state.shortInlineReply) renderShortShareBar();
      }).catch(() => {});
      return;
    }
    if (payload.type === "room_left") {
      const selfLeft = payload.username === state.messenger.user?.username;
      if (selfLeft || !payload.room) {
        removeMessengerRoom(payload.roomId);
        if (payload.roomId === state.selectedRoomId) closeChatRoom();
      } else {
        const room = upsertMessengerRoom(payload.room);
        if (room?.id === state.selectedRoomId) {
          renderChatRoom();
          void loadChatMessages({ markRead: false });
        }
      }
      if (isShortsView) renderShortShareBar();
      else renderChats();
      return;
    }
    if (payload.type === "presence_updated") {
      applyPresenceEvent(payload);
      if (isShortsView) renderShortShareBar();
      else renderMessenger();
      return;
    }
    void loadMessenger(!isShortsView).then(() => {
      if (isShortsView && !state.shortInlineReply) renderShortShareBar();
    }).catch(() => {});
  };
  source.onerror = () => {
    source.close();
    if (state.eventSource === source) {
      state.eventSource = null;
      state.eventConnected = false;
      startLiveSync();
      window.clearTimeout(state.eventReconnectTimer);
      state.eventReconnectTimer = window.setTimeout(connectEvents, 1200);
    }
  };
}

function renderFriends() {
  friendList.replaceChildren();
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
    item.addEventListener("click", () => openDirectChat(friend.id));
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
