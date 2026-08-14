"use strict";

// Application orchestration and feature-level state transitions.
function renderMessenger() {
  const user = state.messenger.user || state.session?.user;
  appScreen.classList.remove("guest-mode");
  appScreen.classList.toggle("shorts-mode", state.activeList === "shorts");
  appTitle.textContent = state.activeList === "friends" ? "친구" : "채팅";
  openLoginButton.classList.add("hidden");
  openProfileButton.classList.remove("hidden");
  openDirectoryButton.classList.remove("hidden");
  logoutButton.classList.remove("hidden");
  chatsTab.classList.toggle("active", state.activeList === "chats");
  friendsTab.classList.toggle("active", state.activeList === "friends");
  shortsTab.classList.toggle("active", state.activeList === "shorts");
  shortsSoundToggle.classList.toggle("hidden", state.activeList !== "shorts");
  chatList.classList.toggle("hidden", state.activeList !== "chats");
  friendList.classList.toggle("hidden", state.activeList !== "friends");
  shortsView.classList.toggle("hidden", state.activeList !== "shorts");
  if (state.activeList === "chats") renderChats();
  if (state.activeList === "friends") renderFriends();
  if (state.activeList === "shorts") {
    renderShorts();
    renderShortShareBar();
  }
  if (!directorySheet.classList.contains("hidden")) renderDirectory();
}

function applyMessengerData(data) {
  const incomingMessages = [];
  const friends = data.friends || [];
  const friendsById = new Map(friends.map((friend) => [friend.id, friend]));
  const rooms = (data.rooms || []).map((room) => {
    const friend = room.peer?.id ? friendsById.get(room.peer.id) : null;
    return friend ? { ...room, peer: { ...friend, ...room.peer } } : room;
  });
  rooms.forEach((room) => {
    const message = room.last_message;
    if (!message?.id) return;
    const previousMessageId = state.lastSeenRoomMessageIds[room.id];
    if (state.liveSyncInitialized && previousMessageId !== message.id && message.username !== data.user?.username) {
      incomingMessages.push({ roomId: room.id, message });
    }
    state.lastSeenRoomMessageIds[room.id] = message.id;
  });
  state.liveSyncInitialized = true;
  state.messenger = {
    user: data.user,
    friends,
    discoverableUsers: data.discoverable_users || [],
    rooms,
  };
  return incomingMessages;
}

async function loadMessenger(render = true) {
  const data = await api("/messenger");
  const incomingMessages = applyMessengerData(data);
  if (render) renderMessenger();
  return incomingMessages;
}

async function syncLiveState() {
  if (!state.session?.user || state.liveSyncBusy) return;
  state.liveSyncBusy = true;
  try {
    const isShortsView = state.activeList === "shorts";
    const [incomingMessages] = await Promise.all([
      loadMessenger(!isShortsView),
      state.selectedRoomId ? loadChatMessages() : Promise.resolve(),
    ]);
    if (isShortsView) {
      const incoming = incomingMessages[0];
      const room = incoming && state.messenger.rooms.find((candidate) => candidate.id === incoming.roomId);
      if (!state.shortInlineReply && incoming && room) showShortMessageNotice(room, incoming.message);
      else if (!state.shortInlineReply) renderShortShareBar();
    } else {
      renderChatRoom();
    }
  } catch (_) {
  } finally {
    state.liveSyncBusy = false;
  }
}

function startLiveSync() {
  if (state.eventConnected || state.liveSyncTimer) return;
  state.liveSyncTimer = window.setInterval(syncLiveState, 15000);
}

function stopLiveSync() {
  window.clearInterval(state.liveSyncTimer);
  state.liveSyncTimer = null;
}

async function startApp() {
  state.isGuest = false;
  showApp();
  try {
    await loadMessenger();
    connectEvents();
    startLiveSync();
    window.clearTimeout(state.appStartRetryTimer);
    state.appStartRetryTimer = null;
    state.appStartRetryCount = 0;
    openStatusEmojiPicker();
    setAppStatus("친구를 추가하거나 새 대화를 시작해 보세요.");
  } catch (error) {
    setAppStatus(`${error.message} 연결되면 자동으로 다시 시도합니다.`, "error");
    const retryDelay = Math.min(30000, 1000 * (2 ** Math.min(state.appStartRetryCount, 5)));
    state.appStartRetryCount += 1;
    window.clearTimeout(state.appStartRetryTimer);
    state.appStartRetryTimer = window.setTimeout(() => {
      if (state.session?.user) startApp();
    }, retryDelay);
  }
}

async function loadOlderChatMessages() {
  if (!state.selectedRoomId || !state.messagesNextCursor || state.messagesLoadingOlder) return;
  const roomId = state.selectedRoomId;
  const cursor = state.messagesNextCursor;
  const previousScrollHeight = chatMessageList.scrollHeight;
  state.messagesLoadingOlder = true;
  try {
    const payload = await api(
      `/messages?room_id=${encodeURIComponent(roomId)}&limit=30&before=${encodeURIComponent(cursor)}`,
    );
    if (state.selectedRoomId !== roomId || state.messagesNextCursor !== cursor) return;
    const olderMessages = payload.items || [];
    if (olderMessages.length) {
      const existingIds = new Set();
      for (const message of state.messages) existingIds.add(message.id);
      const uniqueOlderMessages = [];
      for (const message of olderMessages) {
        if (!existingIds.has(message.id)) uniqueOlderMessages.push(message);
      }
      if (uniqueOlderMessages.length) {
        state.messages = [...uniqueOlderMessages, ...state.messages];
        rebuildMessageIndexes();
        state.messageRevision += 1;
        renderChatRoom({ preserveScrollHeight: previousScrollHeight });
      }
    }
    state.messagesNextCursor = payload.next_cursor || "";
  } catch (error) {
    setAppStatus(error.message, "error");
  } finally {
    if (state.selectedRoomId === roomId) state.messagesLoadingOlder = false;
  }
}

function setActiveList(listName) {
  if (state.activeList === listName) return;
  if (listName === "shorts") {
    state.youtube.feedVersion += 1;
    state.youtube.guestVideos = [];
    state.youtube.guestCursor = "";
    state.youtube.guestError = "";
    state.youtube.guestLoading = false;
    state.youtube.renderedFeedVersion = -1;
    state.youtube.renderedGuestVideoCount = 0;
    shortsView.scrollTop = 0;
  }
  state.activeList = listName;
  renderMessenger();
  if (listName === "shorts") {
    loadGuestShorts(true);
  }
}

function openDirectory() {
  renderDirectory();
  renderGroupMemberList();
  directorySheet.classList.remove("hidden");
  friendCodeInput.focus();
}

function closeDirectory() {
  directorySheet.classList.add("hidden");
}

function selectedGroupMemberIds() {
  return Array.from(groupMemberList.querySelectorAll('input[type="checkbox"]:checked'))
    .map((input) => input.value);
}

function syncGroupCreateButton() {
  const memberCount = selectedGroupMemberIds().length;
  createGroupRoomButton.disabled = !groupRoomName.value.trim() || memberCount < 2 || memberCount > 49;
}

function renderGroupMemberList() {
  groupMemberList.replaceChildren();
  if (!state.messenger.friends.length) {
    const empty = document.createElement("p");
    empty.className = "group-member-empty";
    empty.textContent = "그룹을 만들려면 먼저 친구를 추가하세요.";
    groupMemberList.appendChild(empty);
    syncGroupCreateButton();
    return;
  }
  for (const friend of state.messenger.friends) {
    const option = document.createElement("label");
    option.className = "group-member-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = friend.id;
    const name = document.createElement("span");
    name.textContent = getDisplayName(friend);
    option.append(checkbox, name);
    groupMemberList.appendChild(option);
  }
  syncGroupCreateButton();
}

async function createGroupChat() {
  const name = groupRoomName.value.trim();
  const memberUserIds = selectedGroupMemberIds();
  if (!name || memberUserIds.length < 2 || memberUserIds.length > 49) {
    setAppStatus("그룹 이름을 입력하고 친구를 2명 이상 선택해 주세요.", "error");
    syncGroupCreateButton();
    return;
  }
  createGroupRoomButton.disabled = true;
  try {
    const data = await api("/rooms", {
      method: "POST",
      body: JSON.stringify({ name, memberUserIds }),
    });
    state.activeList = "chats";
    upsertMessengerRoom(data.room);
    groupRoomName.value = "";
    closeDirectory();
    renderMessenger();
    await openChatRoom(data.room.id);
    void loadMessenger(false).then(renderChats).catch(() => {});
    setAppStatus(`${data.room.name} 그룹을 만들었어요.`, "success");
  } catch (error) {
    setAppStatus(error.message, "error");
  } finally {
    syncGroupCreateButton();
  }
}

async function addFriend(friendCode) {
  const normalizedFriendCode = friendCode.trim();
  if (!normalizedFriendCode) {
    setAppStatus("친구 ID를 입력해 주세요.", "error");
    friendCodeInput.focus();
    return;
  }
  try {
    const data = await api("/friends", {
      method: "POST",
      body: JSON.stringify({ friendCode: normalizedFriendCode }),
    });
    await loadMessenger();
    friendCodeInput.value = "";
    closeDirectory();
    setAppStatus(`${data.friend.username} 님을 친구로 추가했어요.`, "success");
  } catch (error) {
    setAppStatus(error.message, "error");
  }
}

async function openDirectChat(userId) {
  try {
    const data = await api("/direct-rooms", {
      method: "POST",
      body: JSON.stringify({ userId }),
    });
    state.activeList = "chats";
    const existingIndex = state.messenger.rooms.findIndex((room) => room.id === data.room.id);
    if (existingIndex >= 0) state.messenger.rooms.splice(existingIndex, 1, data.room);
    else state.messenger.rooms.unshift(data.room);
    renderMessenger();
    await openChatRoom(data.room.id);
    loadMessenger(false).then(() => {
      if (state.activeList === "shorts") renderShortShareBar();
      else {
        renderChats();
        renderFriends();
        renderChatRoom();
      }
    }).catch(() => {});
    setAppStatus(data.created ? `${data.room.name} 님과의 채팅방을 만들었어요.` : "기존 채팅방을 열었어요.", "success");
  } catch (error) {
    setAppStatus(error.message, "error");
  }
}
