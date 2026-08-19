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

function renderChatActionBar() {
  const context = state.actionBarByTab.chats;
  resetActionBarControls("채팅 작업");
  if (context.mode === "composing") {
    const input = document.createElement("input");
    input.type = "search";
    input.className = "context-search";
    input.placeholder = "채팅방 또는 최근 메시지 검색";
    input.setAttribute("aria-label", "채팅 검색");
    input.value = context.query;
    input.addEventListener("input", () => {
      context.query = input.value;
      renderChats();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      context.query = "";
      context.mode = "idle";
      renderChats();
      renderShortShareBar(true);
    });
    shortShareList.appendChild(input);
    shortShareSend.classList.remove("hidden");
    ColorlessPlatform.decorateIconButton(shortShareSend, "x", { label: "검색 닫기", iconOnly: true });
    requestAnimationFrame(() => input.focus());
    return;
  }

  shortShareList.append(
    createContextAction("새 채팅", "message-plus", openNewChat),
    createContextAction("검색", "search", () => {
      context.mode = "composing";
      renderShortShareBar(true);
    }),
  );
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

  context.mode = "idle";
  context.selection = [];
  shortShareList.append(
    createContextAction("친구 추가", "user-plus", openDirectory),
    createContextAction("새 채팅", "message-plus", openNewChat),
  );
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
  context.selection = [friendId];
  renderShortShareBar(true);
}

async function handleContextActionPrimary() {
  if (state.shortInlineReply || state.shortMessageNotice || state.activeList === "shorts") {
    await handleShortShareAction();
    return;
  }
  const context = activeActionBarState();
  if (state.activeList === "chats" && context.mode === "composing") {
    context.mode = "idle";
    context.query = "";
    renderChats();
    renderShortShareBar(true);
    return;
  }
  if (state.activeList === "friends" && context.mode === "selecting" && context.selection[0]) {
    const friendId = context.selection[0];
    context.mode = "idle";
    context.selection = [];
    await openDirectChat(friendId);
  }
}
