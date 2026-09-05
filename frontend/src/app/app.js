"use strict";

import { CHAT_MESSAGE_PAGE_SIZE, appScreen, appTitle, chatList, chatsTab, createAvatar, createNewChatButton, directorySheet, friendCodeInput, friendList, friendsTab, getDisplayName, myDisplayName, myFriendCode, myProfileAvatar, myTab, myView, newChatGroupName, newChatGroupNameField, newChatMemberList, newChatSearch, newChatSheet, openDirectoryButton, openLoginButton, openNewChatButton, realtimeEvents, registerCoreHooks, renderStatusEmojiControl, requestAction, setAppStatus, shortShareBar, showApp, state, syncAppStatusForActiveTab } from "./core.js";
import { connectEvents, rebuildPresenceIndexes, registerRealtimeHandlers, renderChats, renderDirectory, renderFriends, upsertMessengerRoom } from "./messenger.js";
import { openChatRoom, rebuildMessageIndexes, renderChatRoom } from "./chat.js";
import { captureChatVirtualAnchor, chatVirtualScrollTopForAnchor } from "./chat-virtual.js";
import { renderWorkModeControl, syncWorkModeVisibility } from "./work-mode.js";
import { activeActionBarState, renderContextActionBar, renderFriendActionBar, renderHeaderSearch } from "./action-bar.js";
import { mergeAuthoritativeRoomSnapshot, mergeRealtimeRoomSnapshot } from "./platform/room-snapshots.js";

const accountIdentifier = document.getElementById("account-identifier");
const identitySwitcher = document.getElementById("identity-switcher");
const identityCreateForm = document.getElementById("identity-create-form");
const identityUsername = document.getElementById("identity-username");
const identityDisplayName = document.getElementById("identity-display-name");
const identityCreateButton = document.getElementById("identity-create-button");
const identityFormStatus = document.getElementById("identity-form-status");
const identityCreateStatus = document.getElementById("identity-create-status");
const identityCreateModal = document.getElementById("identity-create-modal");
const openIdentityCreateButton = document.getElementById("open-identity-create-button");
const closeIdentityCreateButton = document.getElementById("close-identity-create-button");
const ticketAdminButton = document.getElementById("open-ticket-admin-button");

// Application orchestration and feature-level state transitions.
const APP_CHAT_PAGE_SIZE = typeof CHAT_MESSAGE_PAGE_SIZE === "number" ? CHAT_MESSAGE_PAGE_SIZE : 30;

function renderMessenger() {
  const user = state.messenger.user || state.session?.user;
  appScreen.classList.remove("guest-mode");
  appTitle.textContent = ({ chats: "채팅", friends: "친구", my: "MY" })[state.activeList] || "채팅";
  openLoginButton.classList.add("hidden");
  renderStatusEmojiControl();
  syncAppStatusForActiveTab();
  renderHeaderSearch();
  openDirectoryButton.classList.toggle("hidden", state.activeList !== "friends");
  openNewChatButton.classList.toggle("hidden", state.activeList !== "chats");
  chatsTab.classList.toggle("active", state.activeList === "chats");
  friendsTab.classList.toggle("active", state.activeList === "friends");
  myTab.classList.toggle("active", state.activeList === "my");
  chatList.classList.toggle("hidden", state.activeList !== "chats");
  friendList.classList.toggle("hidden", state.activeList !== "friends");
  myView.classList.toggle("hidden", state.activeList !== "my");
  if (state.activeList === "chats") renderChats();
  if (state.activeList === "friends") renderFriends();
  if (state.activeList === "my") renderMy();
  syncWorkModeVisibility();
  renderContextActionBar();
  shortShareBar.classList.toggle("hidden", state.activeList === "my");
  if (!directorySheet.classList.contains("hidden")) renderDirectory();
}

function resetApplicationUi() {
  identityCreateModal.classList.add("hidden");
  identityCreateForm.reset();
  identityFormStatus.textContent = "";
  identityCreateStatus.textContent = "";
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
  myFriendCode.textContent = `@${user.username}`;
  const identities = state.session?.identities || [user];
  const activeIdentityId = state.session?.active_identity_id || user.id;
  identitySwitcher.replaceChildren(...identities.map((identity) => {
    const option = document.createElement("option");
    option.value = identity.id;
    option.textContent = `${getDisplayName(identity)} (@${identity.username})`;
    option.selected = identity.id === activeIdentityId;
    return option;
  }));
  identitySwitcher.disabled = identities.length < 2;
  const account = state.session?.account;
  accountIdentifier.textContent = `${identities.length}/${account?.identity_limit || 3}개의 활동 ID 사용 중`;
  openIdentityCreateButton.classList.toggle("hidden", identities.length >= Number(account?.identity_limit || 3));
  ticketAdminButton?.classList.toggle("hidden", !account?.is_ticket_admin);
  renderStatusEmojiControl();
  renderWorkModeControl();
}

async function switchIdentity() {
  const identityId = identitySwitcher.value;
  if (!identityId || identityId === state.session?.active_identity_id) return;
  identitySwitcher.disabled = true;
  identityFormStatus.textContent = "활동 ID를 전환하고 있어요.";
  try {
    await requestAction("identities.switch", "/identities/switch", {
      method: "POST",
      body: JSON.stringify({ identityId }),
    });
    window.location.reload();
  } catch (error) {
    identityFormStatus.textContent = error.message;
    identitySwitcher.value = state.session?.active_identity_id || "";
    identitySwitcher.disabled = false;
  }
}

async function createIdentity(event) {
  event.preventDefault();
  identityCreateButton.disabled = true;
  identityCreateStatus.textContent = "새 활동 ID를 만들고 있어요.";
  try {
    const payload = await requestAction("identities.create", "/identities", {
      method: "POST",
      body: JSON.stringify({
        username: identityUsername.value.trim(),
        displayName: identityDisplayName.value.trim(),
      }),
    });
    state.session = {
      ...state.session,
      account: payload.account,
      identities: payload.identities || state.session?.identities || [],
      active_identity_id: payload.active_identity_id || state.session?.active_identity_id,
    };
    identityCreateForm.reset();
    identityFormStatus.textContent = "새 활동 ID를 만들었어요.";
    identityCreateModal.classList.add("hidden");
    renderMy();
  } catch (error) {
    identityCreateStatus.textContent = error.message;
  } finally {
    identityCreateButton.disabled = false;
  }
}

identitySwitcher.addEventListener("change", () => void switchIdentity());
identityCreateForm.addEventListener("submit", (event) => void createIdentity(event));
openIdentityCreateButton.addEventListener("click", () => {
  identityCreateForm.reset();
  identityCreateStatus.textContent = "";
  identityCreateModal.classList.remove("hidden");
  identityDisplayName.focus();
});
closeIdentityCreateButton.addEventListener("click", () => identityCreateModal.classList.add("hidden"));
identityCreateModal.addEventListener("click", (event) => { if (event.target === identityCreateModal) identityCreateModal.classList.add("hidden"); });

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
  const currentRooms = new Map(state.messenger.rooms.map((room) => [room.id, room]));
  const roomSnapshots = (data.rooms || []).map(
    (room) => mergeAuthoritativeRoomSnapshot(currentRooms.get(room.id), room),
  );
  const mergedRooms = mergeEntitiesById(state.messenger.rooms, roomSnapshots, resetRooms);
  const rooms = mergedRooms.map((room) => {
    const resolved = room.last_message?.id
      ? state.messageEventJournal.resolve(room.id, room.last_message)
      : null;
    if (resolved?.deleted) room = resolved.room
      ? mergeRealtimeRoomSnapshot(room, resolved.room)
      : { ...room, last_message: null };
    else if (resolved?.message) room = { ...room, last_message: resolved.message };
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
  const authEpoch = state.authEpoch;
  state.friendsLoading = true;
  try {
    const cursor = reset ? "" : state.friendsNextCursor;
    const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    const page = await requestAction("friends.page", `/friends?limit=30${suffix}`, {}, {
      key: `friends.page:${authEpoch}:${cursor || "first"}`,
      policy: "join",
    });
    if (state.authEpoch !== authEpoch) return [];
    state.friendsNextCursor = page.next_cursor || "";
    applyMessengerData({ friends: page.items || [] }, { resetFriends: reset, resetRooms: false });
    if (render) {
      renderFriends();
      renderFriendActionBar();
    }
    return page.items || [];
  } finally {
    if (state.authEpoch === authEpoch) state.friendsLoading = false;
  }
}

async function loadRoomsPage({ reset = false, render = false } = {}) {
  if (state.roomsLoading) {
    if (reset) {
      state.roomsResetPending = true;
      state.roomsResetRender ||= render;
    }
    return [];
  }
  if (!reset && !state.roomsNextCursor) return [];
  const authEpoch = state.authEpoch;
  state.roomsLoading = true;
  try {
    const cursor = reset ? "" : state.roomsNextCursor;
    const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    const page = await requestAction("rooms.page", `/rooms?limit=30${suffix}`, {}, {
      key: `rooms.page:${authEpoch}:${cursor || "first"}`,
      policy: "join",
    });
    if (state.authEpoch !== authEpoch) return [];
    state.roomsNextCursor = page.next_cursor || "";
    applyMessengerData({ rooms: page.items || [] }, { resetFriends: false, resetRooms: reset });
    if (render) {
      renderChats();
      renderContextActionBar();
    }
    return page.items || [];
  } finally {
    if (state.authEpoch === authEpoch) {
      state.roomsLoading = false;
      if (state.roomsResetPending) {
        const pendingRender = state.roomsResetRender;
        state.roomsResetPending = false;
        state.roomsResetRender = false;
        void loadRoomsPage({ reset: true, render: pendingRender }).catch(() => {});
      }
    }
  }
}

async function loadMessenger(render = true) {
  const authEpoch = state.authEpoch;
  const me = await requestAction("messenger.me", "/me", {}, {
    key: `messenger.load:${authEpoch}`,
    policy: "join",
  });
  if (state.authEpoch !== authEpoch || !state.session?.user) return false;
  const [friendsPage, roomsPage] = await Promise.all([
    requestAction("friends.first-page", "/friends?limit=30"),
    requestAction("rooms.first-page", "/rooms?limit=30"),
  ]);
  if (state.authEpoch !== authEpoch || !state.session?.user) return false;
  state.friendsNextCursor = friendsPage.next_cursor || "";
  state.roomsNextCursor = roomsPage.next_cursor || "";
  const user = { ...(state.session?.user || {}), ...(me.user || {}) };
  state.session = {
    ...state.session,
    user,
    account: me.account || state.session?.account,
    identities: me.identities || state.session?.identities || [user],
    active_identity_id: me.active_identity_id || state.session?.active_identity_id || user.id,
  };
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
  const authEpoch = state.authEpoch;
  state.liveSyncBusy = true;
  try {
    let hasMore = true;
    while (hasMore) {
      const payload = await requestAction(
        "messenger.sync",
        `/sync?after_revision=${encodeURIComponent(state.syncRevision)}&limit=200`,
      );
      if (state.authEpoch !== authEpoch || !state.session?.user) return;
      if (payload.reset_required) {
        await loadMessenger();
        break;
      }
      for (const event of payload.events || []) {
        const handled = await realtimeEvents.dispatch(event, {});
        if (state.authEpoch !== authEpoch) return;
        if (!handled) {
          await loadMessenger();
          realtimeEvents.markHandled?.(event);
        }
        recordSyncRevision(event.revision);
      }
      recordSyncRevision(payload.revision);
      hasMore = Boolean(payload.has_more);
    }
  } catch (_) {
  } finally {
    if (state.authEpoch === authEpoch) state.liveSyncBusy = false;
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
  const authEpoch = state.authEpoch;
  state.isGuest = false;
  showApp();
  try {
    registerRealtimeHandlers();
    if (!await loadMessenger() || state.authEpoch !== authEpoch) return;
    await syncLiveState();
    if (state.authEpoch !== authEpoch || !state.session?.user) return;
    connectEvents();
    startLiveSync();
    window.clearTimeout(state.appStartRetryTimer);
    state.appStartRetryTimer = null;
    state.appStartRetryCount = 0;
    setAppStatus("");
  } catch (error) {
    if (state.authEpoch !== authEpoch) return;
    setAppStatus(`${error.message} 연결되면 자동으로 다시 시도합니다.`, "error");
    const retryDelay = Math.min(30000, 1000 * (2 ** Math.min(state.appStartRetryCount, 5)));
    state.appStartRetryCount += 1;
    window.clearTimeout(state.appStartRetryTimer);
    state.appStartRetryTimer = window.setTimeout(() => {
      if (state.authEpoch === authEpoch && state.session?.user) startApp();
    }, retryDelay);
  }
}

async function loadOlderChatMessages() {
  if (!state.selectedRoomId || !state.messagesNextCursor || state.messagesLoadingOlder) return;
  const roomId = state.selectedRoomId;
  const authEpoch = state.authEpoch;
  const cursor = state.messagesNextCursor;
  state.messagesLoadingOlder = true;
  const controller = new AbortController();
  state.messagesOlderLoadController?.abort();
  state.messagesOlderLoadController = controller;
  try {
    const payload = await requestAction(
      "messages.load-older",
      `/messages?room_id=${encodeURIComponent(roomId)}&limit=${APP_CHAT_PAGE_SIZE}&before=${encodeURIComponent(cursor)}`,
      { signal: controller.signal },
    );
    if (state.authEpoch !== authEpoch || state.selectedRoomId !== roomId || state.messagesNextCursor !== cursor) return;
    const olderMessages = state.messageEventJournal.resolveAll(roomId, payload.items || []);
    state.messagesNextCursor = payload.next_cursor || "";
    if (olderMessages.length) {
      const existingIds = new Set();
      for (const message of state.messages) existingIds.add(message.id);
      const uniqueOlderMessages = [];
      for (const message of olderMessages) {
        if (!existingIds.has(message.id)) uniqueOlderMessages.push(message);
      }
      if (uniqueOlderMessages.length) {
        const anchor = captureChatVirtualAnchor();
        state.messages = [...uniqueOlderMessages, ...state.messages];
        rebuildMessageIndexes();
        state.messageRevision += 1;
        renderChatRoom({ restoreScrollTop: chatVirtualScrollTopForAnchor(anchor) });
      }
    }
  } catch (error) {
    if (error?.name === "AbortError") return;
    setAppStatus(error.message, "error");
  } finally {
    if (state.messagesOlderLoadController === controller) {
      state.messagesOlderLoadController = null;
      if (state.selectedRoomId === roomId) state.messagesLoadingOlder = false;
    }
  }
}

function setActiveList(listName) {
  if (state.activeList === listName) return;
  state.activeList = listName;
  renderMessenger();
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
  renderContextActionBar();
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
      renderContextActionBar();
      renderChats();
      renderFriends();
      renderChatRoom();
    }).catch(() => {});
    setAppStatus(data.created ? `${data.room.name} 님과의 채팅방을 만들었어요.` : "기존 채팅방을 열었어요.", "success");
    return data.room;
  } catch (error) {
    setAppStatus(error.message, "error");
    return null;
  }
}

registerCoreHooks({ renderMessenger, resetApplicationUi });

export {
  addFriend,
  closeDirectory,
  closeNewChat,
  createNewChat,
  loadFriendsPage,
  loadMessenger,
  loadOlderChatMessages,
  loadRoomsPage,
  mergeEntitiesById,
  openDirectChat,
  openDirectory,
  openNewChat,
  recordSyncRevision,
  renderMessenger,
  renderNewChatMemberList,
  setActiveList,
  startApp,
  startLiveSync,
  stopLiveSync,
  syncLiveState,
  syncNewChatCreateButton,
  updateNewChatMemberSelection,
};
