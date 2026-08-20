"use strict";

// Shared context action bar for chat, friend, shorts, notification, and input modes.
function activeActionBarState() {
  return state.actionBarByTab[state.activeList];
}

function createContextAction(label, icon, handler, options = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = options.chip ? "context-chip" : "context-action";
  if (options.pressed !== undefined) button.setAttribute("aria-pressed", String(options.pressed));
  button.textContent = label;
  if (icon) ColorlessPlatform.decorateIconButton(button, icon, { label, visibleLabel: !options.chip });
  if (options.chip) button.setAttribute("aria-label", options.ariaLabel || label);
  button.addEventListener("click", handler);
  return button;
}

function resetActionBarControls(label) {
  shortShareBar.setAttribute("aria-label", label);
  shortShareBar.classList.remove("replying");
  shortShareList.replaceChildren();
  shortShareFeedback.textContent = "";
  shortMessageToggle.classList.add("hidden");
  shortShareSend.classList.add("hidden");
  shortShareSend.disabled = false;
}

function renderHeaderSearch({ focus = false } = {}) {
  const context = activeActionBarState();
  const searchable = state.activeList === "chats" || state.activeList === "friends";
  const searching = searchable && context?.mode === "composing";
  openListSearchButton.classList.toggle("hidden", !searchable || searching);
  appTitle.classList.toggle("hidden", searching);
  headerSearch.classList.toggle("hidden", !searching);
  appHeader.classList.toggle("searching", searching);
  if (!searching) return;
  headerSearchInput.placeholder = state.activeList === "chats"
    ? "사람 또는 주고받은 대화 검색"
    : "친구 이름 또는 ID 검색";
  headerSearchInput.setAttribute("aria-label", state.activeList === "chats" ? "채팅 검색" : "친구 검색");
  if (headerSearchInput.value !== context.query) headerSearchInput.value = context.query;
  if (focus) requestAnimationFrame(() => headerSearchInput.focus({ preventScroll: true }));
}

function openListSearch() {
  if (state.activeList !== "chats" && state.activeList !== "friends") return;
  const context = activeActionBarState();
  context.mode = "composing";
  context.selection = [];
  renderHeaderSearch({ focus: true });
  renderShortShareBar(true);
}

function closeListSearch() {
  const context = activeActionBarState();
  if (!context) return;
  context.mode = "idle";
  context.query = "";
  context.selection = [];
  if (state.activeList === "chats") {
    resetChatSearch();
    renderChats();
  } else if (state.activeList === "friends") {
    renderFriends();
  }
  renderHeaderSearch();
  renderShortShareBar(true);
}

function updateHeaderSearch() {
  const context = activeActionBarState();
  if (!context || context.mode !== "composing") return;
  context.query = headerSearchInput.value;
  if (state.activeList === "chats") {
    renderChats();
    scheduleChatSearch(headerSearchInput.value);
  } else if (state.activeList === "friends") {
    renderFriends();
  }
}

function renderChatActionBar() {
  const context = state.actionBarByTab.chats;
  resetActionBarControls("채팅 작업");
  const unreadCount = state.messenger.rooms.filter((room) => room.unread_count > 0).length;
  shortShareList.appendChild(createContextAction(
    `안 읽음 ${unreadCount}`,
    "message-circle",
    () => {
      context.filter = context.filter === "unread" ? "all" : "unread";
      renderChats();
      renderShortShareBar(true);
    },
    { pressed: context.filter === "unread" },
  ));
  for (const room of recentChatRooms().slice(0, 5)) {
    shortShareList.appendChild(createContextAction(
      room.name,
      "",
      () => openChatRoom(room.id),
      { chip: true, ariaLabel: `${room.name} 채팅방 열기` },
    ));
  }
}

function renderFriendActionBar() {
  const context = state.actionBarByTab.friends;
  resetActionBarControls("친구 작업");
  const selectedId = context.selection[0];
  const selected = state.messenger.friends.find((friend) => friend.id === selectedId);
  if (context.mode === "selecting" && selected) {
    const summary = document.createElement("span");
    summary.className = "context-summary";
    summary.textContent = `${getDisplayName(selected)} · ${selected.presence?.online ? "온라인" : (selected.status_message || "오프라인")}`;
    shortShareList.appendChild(summary);
    shortShareList.appendChild(createContextAction("선택 취소", "x", () => {
      context.mode = "idle";
      context.selection = [];
      renderShortShareBar(true);
    }));
    shortShareSend.classList.remove("hidden");
    ColorlessPlatform.decorateIconButton(shortShareSend, "message-circle", { label: `${getDisplayName(selected)}님과 채팅 시작`, iconOnly: true });
    return;
  }

  if (context.mode !== "composing") {
    context.mode = "idle";
    context.selection = [];
  }
  const onlineFriends = state.messenger.friends.filter((friend) => friend.presence?.online);
  const online = document.createElement("span");
  online.className = "context-summary";
  online.textContent = `온라인 ${onlineFriends.length}`;
  shortShareList.appendChild(online);
  for (const friend of onlineFriends.slice(0, 5)) {
    shortShareList.appendChild(createContextAction(
      getDisplayName(friend),
      "",
      () => selectFriendForActionBar(friend.id),
      { chip: true, ariaLabel: `${getDisplayName(friend)} 프로필 보기` },
    ));
  }
}

function selectFriendForActionBar(friendId) {
  const context = state.actionBarByTab.friends;
  context.mode = "selecting";
  context.query = "";
  context.selection = [friendId];
  renderFriends();
  renderHeaderSearch();
  renderShortShareBar(true);
}

async function handleContextActionPrimary() {
  if (state.shortInlineReply || state.shortMessageNotice || state.activeList === "shorts") {
    await handleShortShareAction();
    return;
  }
  const context = activeActionBarState();
  if (state.activeList === "friends" && context.mode === "selecting" && context.selection[0]) {
    const friendId = context.selection[0];
    context.mode = "idle";
    context.selection = [];
    await openDirectChat(friendId);
  }
}
