"use strict";

// Application orchestration and feature-level state transitions.
function renderMessenger() {
  const user = state.messenger.user || state.session?.user;
  appScreen.classList.remove("guest-mode");
  appScreen.classList.toggle("shorts-mode", state.activeList === "shorts");
  appTitle.textContent = ({ chats: "채팅", friends: "친구", shorts: "쇼츠", my: "MY" })[state.activeList] || "채팅";
  openLoginButton.classList.add("hidden");
  renderStatusEmojiControl();
  syncAppStatusForActiveTab();
  renderHeaderSearch();
  openDirectoryButton.classList.toggle("hidden", state.activeList !== "friends");
  openNewChatButton.classList.toggle("hidden", state.activeList !== "chats");
  chatsTab.classList.toggle("active", state.activeList === "chats");
  friendsTab.classList.toggle("active", state.activeList === "friends");
  shortsTab.classList.toggle("active", state.activeList === "shorts");
  myTab.classList.toggle("active", state.activeList === "my");
  shortsSoundToggle.classList.toggle("hidden", state.activeList !== "shorts");
  chatList.classList.toggle("hidden", state.activeList !== "chats");
  friendList.classList.toggle("hidden", state.activeList !== "friends");
  shortsView.classList.toggle("hidden", state.activeList !== "shorts");
  myView.classList.toggle("hidden", state.activeList !== "my");
  if (state.activeList === "chats") renderChats();
  if (state.activeList === "friends") renderFriends();
  if (state.activeList === "shorts") {
    renderShorts();
  }
  if (state.activeList === "my") renderMy();
  renderShortShareBar();
  shortShareBar.classList.toggle("hidden", state.activeList === "my");
  if (!directorySheet.classList.contains("hidden")) renderDirectory();
}

function renderMy() {
  const user = state.messenger.user || state.session?.user;
  if (!user) return;
  myProfileAvatar.replaceChildren(createAvatar(
    getDisplayName(user),
    user.profile_pixels,
    null,
    user.status_message,
    user.profile_thumbnail_url || user.profile_image_url,
  ));
  myDisplayName.textContent = getDisplayName(user);
  myFriendCode.textContent = user.friend_code ? `친구 ID · ${user.friend_code}` : "친구 ID 없음";
  renderStatusEmojiControl();
}

function mergeEntitiesById(current, incoming, reset = false) {
  const entities = new Map((reset ? [] : current).map((item) => [item.id, item]));
  for (const item of incoming) entities.set(item.id, { ...(entities.get(item.id) || {}), ...item });
  return [...entities.values()];
}

function recordSyncRevision(value) {
  const revision = Number(value || 0);
  if (!Number.isSafeInteger(revision) || revision <= state.syncRevision) return;
  state.syncRevision = revision;
  sessionStorage.setItem("colorless-realtime-cursor", String(revision));
}

function applyMessengerData(data, { resetFriends = true, resetRooms = true } = {}) {
  const incomingMessages = [];
  const friends = mergeEntitiesById(state.messenger.friends, data.friends || [], resetFriends)
    .sort((left, right) => String(left.username).localeCompare(String(right.username)));
  const friendsById = new Map(friends.map((friend) => [friend.id, friend]));
  const mergedRooms = mergeEntitiesById(state.messenger.rooms, data.rooms || [], resetRooms);
  const rooms = mergedRooms.map((room) => {
    const friend = room.peer?.id ? friendsById.get(room.peer.id) : null;
    return friend ? { ...room, peer: { ...friend, ...room.peer } } : room;
  }).sort((left, right) => {
    const updated = String(right.updated_at).localeCompare(String(left.updated_at));
    return updated || String(right.id).localeCompare(String(left.id));
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
    user: data.user || state.messenger.user || state.session?.user,
    friends,
    discoverableUsers: data.discoverable_users || [],
    rooms,
  };
  rebuildPresenceIndexes();
  return incomingMessages;
}

async function loadFriendsPage({ reset = false, render = false } = {}) {
  if (state.friendsLoading || (!reset && !state.friendsNextCursor)) return [];
  state.friendsLoading = true;
  try {
    const cursor = reset ? "" : state.friendsNextCursor;
    const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    const page = await requestAction("friends.page", `/friends?limit=30${suffix}`, {}, {
      key: `friends.page:${cursor || "first"}`,
      policy: "join",
    });
    state.friendsNextCursor = page.next_cursor || "";
    applyMessengerData({ friends: page.items || [] }, { resetFriends: reset, resetRooms: false });
    if (render) {
      renderFriends();
      renderFriendActionBar();
    }
    return page.items || [];
  } finally {
    state.friendsLoading = false;
  }
}

async function loadRoomsPage({ reset = false, render = false } = {}) {
  if (state.roomsLoading || (!reset && !state.roomsNextCursor)) return [];
  state.roomsLoading = true;
  try {
    const cursor = reset ? "" : state.roomsNextCursor;
    const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    const page = await requestAction("rooms.page", `/rooms?limit=30${suffix}`, {}, {
      key: `rooms.page:${cursor || "first"}`,
      policy: "join",
    });
    state.roomsNextCursor = page.next_cursor || "";
    applyMessengerData({ rooms: page.items || [] }, { resetFriends: false, resetRooms: reset });
    if (render) {
      renderChats();
      renderShortShareBar();
    }
    return page.items || [];
  } finally {
    state.roomsLoading = false;
  }
}

async function loadMessenger(render = true) {
  const me = await requestAction("messenger.me", "/me", {}, {
    key: "messenger.load",
    policy: "join",
  });
  const [friendsPage, roomsPage] = await Promise.all([
    requestAction("friends.first-page", "/friends?limit=30"),
    requestAction("rooms.first-page", "/rooms?limit=30"),
  ]);
  state.friendsNextCursor = friendsPage.next_cursor || "";
  state.roomsNextCursor = roomsPage.next_cursor || "";
  const user = { ...(state.session?.user || {}), ...(me.user || {}) };
  const incomingMessages = applyMessengerData({
    user,
    friends: friendsPage.items || [],
    rooms: roomsPage.items || [],
  });
  const baselineRevision = Number(me.revision || 0);
  if (Number.isSafeInteger(baselineRevision) && baselineRevision >= state.syncRevision) {
    state.syncRevision = baselineRevision;
    sessionStorage.setItem("colorless-realtime-cursor", String(baselineRevision));
  }
  if (render) renderMessenger();
  return incomingMessages;
}

async function loadAllFriends() {
  while (state.friendsNextCursor) await loadFriendsPage({ render: false });
  return state.messenger.friends;
}

async function syncLiveState() {
  if (!state.session?.user || state.liveSyncBusy) return;
  state.liveSyncBusy = true;
  try {
    const isShortsView = state.activeList === "shorts";
    let hasMore = true;
    while (hasMore) {
      const payload = await requestAction(
        "messenger.sync",
        `/sync?after_revision=${encodeURIComponent(state.syncRevision)}&limit=200`,
      );
      for (const event of payload.events || []) {
        await realtimeEvents.dispatch(event, { isShortsView });
        recordSyncRevision(event.revision);
      }
      recordSyncRevision(payload.revision);
      hasMore = Boolean(payload.has_more);
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
    registerRealtimeHandlers();
    await loadMessenger();
    if (!state.statusPromptShown && !savedStatusEmoji()) {
      state.statusPromptShown = true;
      openStatusEmojiPicker(null);
    }
    await syncLiveState();
    connectEvents();
    startLiveSync();
    window.clearTimeout(state.appStartRetryTimer);
    state.appStartRetryTimer = null;
    state.appStartRetryCount = 0;
    setAppStatus("");
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
    const payload = await requestAction(
      "messages.load-older",
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
  if (state.activeList === "shorts" && listName !== "shorts") releaseAllShortFrames();
  if (listName === "shorts") {
    state.youtube.feedVersion += 1;
    state.youtube.guestVideos = [];
    state.youtube.guestCursor = "";
    state.youtube.guestError = "";
    state.youtube.guestLoading = false;
    state.youtube.renderedFeedVersion = -1;
    state.youtube.virtualStart = -1;
    state.youtube.virtualEnd = -1;
    state.youtube.virtualHeight = 0;
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
  directorySheet.classList.remove("hidden");
  friendCodeInput.focus();
}

function closeDirectory() {
  directorySheet.classList.add("hidden");
}

function openNewChat() {
  const context = activeActionBarState();
  state.newChatOriginTab = state.activeList;
  context.mode = "selecting";
  context.selection = [];
  newChatSearch.value = "";
  renderNewChatMemberList();
  newChatSheet.classList.remove("hidden");
  newChatSearch.focus();
  if (state.friendsNextCursor) {
    void loadAllFriends().then(() => {
      if (!newChatSheet.classList.contains("hidden")) renderNewChatMemberList();
    }).catch(() => {});
  }
}

function closeNewChat() {
  newChatSheet.classList.add("hidden");
  newChatSearch.value = "";
  newChatGroupName.value = "";
  const origin = state.actionBarByTab[state.newChatOriginTab];
  if (origin) {
    origin.mode = "idle";
    origin.selection = [];
  }
  state.newChatOriginTab = "";
  renderShortShareBar(true);
}

function newChatActionBarState() {
  return state.actionBarByTab[state.newChatOriginTab] || activeActionBarState();
}

function selectedNewChatMemberIds() {
  return Array.from(new Set(newChatActionBarState().selection || []));
}

function syncNewChatCreateButton() {
  const selectedIds = selectedNewChatMemberIds();
  const memberCount = selectedIds.length;
  newChatActionBarState().mode = "selecting";
  newChatActionBarState().selection = selectedIds;
  const isGroup = memberCount >= 2;
  newChatGroupNameField.classList.toggle("hidden", !isGroup);
  createNewChatButton.disabled = memberCount === 0 || (isGroup && !newChatGroupName.value.trim());
  createNewChatButton.textContent = isGroup ? "그룹 만들기" : "채팅 시작";
}

function renderNewChatMemberList() {
  newChatMemberList.replaceChildren();
  if (!state.messenger.friends.length) {
    const empty = document.createElement("p");
    empty.className = "new-chat-member-empty";
    empty.textContent = "먼저 친구를 추가해 주세요.";
    newChatMemberList.appendChild(empty);
    syncNewChatCreateButton();
    return;
  }
  const query = newChatSearch.value.trim().toLocaleLowerCase();
  const matchingFriends = state.messenger.friends.filter((friend) => {
    if (!query) return true;
    return [getDisplayName(friend), friend.username, friend.friend_code]
      .some((value) => String(value || "").toLocaleLowerCase().includes(query));
  });
  if (!matchingFriends.length) {
    const empty = document.createElement("p");
    empty.className = "new-chat-member-empty";
    empty.textContent = "검색 결과가 없어요.";
    newChatMemberList.appendChild(empty);
    syncNewChatCreateButton();
    return;
  }
  const selectedIds = new Set(selectedNewChatMemberIds());
  for (const friend of matchingFriends) {
    const option = document.createElement("label");
    option.className = "new-chat-member-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = friend.id;
    checkbox.checked = selectedIds.has(friend.id);
    const name = document.createElement("span");
    name.textContent = getDisplayName(friend);
    option.append(checkbox, name);
    newChatMemberList.appendChild(option);
  }
  syncNewChatCreateButton();
}

function updateNewChatMemberSelection(event) {
  const checkbox = event.target.closest?.('input[type="checkbox"]');
  if (!checkbox) return;
  const selectedIds = new Set(selectedNewChatMemberIds());
  if (checkbox.checked) selectedIds.add(checkbox.value);
  else selectedIds.delete(checkbox.value);
  newChatActionBarState().selection = Array.from(selectedIds);
  syncNewChatCreateButton();
}

async function createNewChat() {
  const memberUserIds = selectedNewChatMemberIds();
  if (!memberUserIds.length) {
    syncNewChatCreateButton();
    return;
  }
  if (memberUserIds.length === 1) {
    const room = await openDirectChat(memberUserIds[0]);
    if (room) closeNewChat();
    return;
  }

  const name = newChatGroupName.value.trim();
  if (!name || memberUserIds.length > 49) {
    setAppStatus("그룹 이름을 입력하고 친구를 선택해 주세요.", "error");
    syncNewChatCreateButton();
    return;
  }
  createNewChatButton.disabled = true;
  try {
    const data = await requestAction("rooms.create-group", "/rooms", {
      method: "POST",
      body: JSON.stringify({ name, memberUserIds }),
    });
    state.activeList = "chats";
    upsertMessengerRoom(data.room);
    closeNewChat();
    renderMessenger();
    await openChatRoom(data.room.id);
    void loadMessenger(false).then(renderChats).catch(() => {});
    setAppStatus(`${data.room.name} 그룹을 만들었어요.`, "success");
  } catch (error) {
    setAppStatus(error.message, "error");
  } finally {
    syncNewChatCreateButton();
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
    const data = await requestAction("friends.add", "/friends", {
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
    const data = await requestAction("rooms.open-direct", "/direct-rooms", {
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
    return data.room;
  } catch (error) {
    setAppStatus(error.message, "error");
    return null;
  }
}
