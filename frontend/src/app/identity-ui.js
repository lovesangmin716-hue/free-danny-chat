import { chatRoomName, requestAction, setAppStatus, state } from "./core.js";
import { openChatRoom } from "./chat.js";

const windows = new Map();
let openedInitialRoom = false;
let unreadTimer = null;
let unreadBusy = false;

export function refreshIdentityUnread(render) {
  if (!state.session?.user || unreadTimer !== null) return;
  const epoch = state.authEpoch;
  unreadTimer = window.setTimeout(async () => {
    unreadTimer = null;
    if (epoch !== state.authEpoch || unreadBusy) return;
    unreadBusy = true;
    try {
      const result = await requestAction("identities.unread", "/identities/unread");
      if (epoch !== state.authEpoch) return;
      state.identityUnread = result;
      document.title = result.total ? `(${result.total > 99 ? "99+" : result.total}) Colorless` : "Colorless";
      render();
    } catch (_) { /* Keep the last confirmed count while offline. */ }
    finally { unreadBusy = false; }
  }, 250);
}

export function renderIdentityControls() {
  const parent = document.getElementById("identity-switcher").parentElement;
  let button = document.getElementById("disable-identity-button");
  if (!button) {
    button = document.createElement("button");
    button.id = "disable-identity-button";
    button.type = "button";
    button.className = "secondary-button";
    button.textContent = "ID 관리";
    button.style.cssText = "min-height:32px;padding:0 8px;font-size:12px;white-space:nowrap";
    parent.style.gridTemplateColumns = "minmax(0,1fr) 32px auto";
    button.addEventListener("click", openIdentityManagement);
    parent.append(button);
  }
  button.disabled = (state.session?.identities?.length || 0) < 2;
}

function openIdentityManagement() {
  document.getElementById("identity-management")?.remove();
  const dialog = document.createElement("dialog");
  dialog.id = "identity-management";
  dialog.setAttribute("aria-label", "활동 ID 관리");
  const description = document.createElement("p");
  description.textContent = "비활성화하면 새 활동이 차단됩니다. 기존 대화와 메시지는 보존되며 이 ID를 다시 사용할 수 없습니다. 현재 ID는 다른 ID로 전환한 뒤 관리할 수 있습니다.";
  dialog.append(description);
  for (const identity of state.session.identities) {
    if (identity.id === state.session.active_identity_id) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `@${identity.username} 비활성화`;
    button.addEventListener("click", async () => {
      if (!window.confirm(`@${identity.username}의 활동을 중지할까요? 대화 기록은 보존됩니다.`)) return;
      button.disabled = true;
      try {
        await requestAction("identities.disable", "/identities/disable", {
          method: "POST", body: JSON.stringify({ identityId: identity.id }),
        });
        window.location.reload();
      } catch (error) {
        description.textContent = error.message;
        button.disabled = false;
      }
    });
    dialog.append(button);
  }
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "닫기";
  close.addEventListener("click", () => dialog.close());
  dialog.append(close);
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  dialog.showModal();
}

export function renderRoomIdentity() {
  const room = state.roomById.get(state.selectedRoomId);
  let label = document.getElementById("chat-actor-label");
  if (!label) {
    label = document.createElement("small");
    label.id = "chat-actor-label";
    label.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0";
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:6px";
    row.append(label);
    chatRoomName.parentElement.append(row);
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "새 창";
    button.style.cssText = "width:auto;min-height:22px;padding:0 6px;font-size:11px;flex-shrink:0";
    button.setAttribute("aria-label", "이 활동 ID로 대화창 따로 열기");
    button.addEventListener("click", openRoomWindow);
    row.append(button);
  }
  label.textContent = room?.viewer_identity?.username ? `@${room.viewer_identity.username}로 대화 중` : "";
}

function openRoomWindow() {
  const room = state.roomById.get(state.selectedRoomId);
  if (!room?.viewer_identity_id) return;
  const key = `${room.viewer_identity_id}:${room.id}`;
  for (const [id, popup] of windows) if (popup.closed) windows.delete(id);
  if (windows.has(key)) return windows.get(key).focus();
  if (windows.size >= 2 || new URLSearchParams(window.location.search).has("chatRoom")) {
    setAppStatus("메인 창에서 대화창을 최대 2개 더 열 수 있어요.");
    return;
  }
  const url = new URL("/", window.location.href);
  url.searchParams.set("chatRoom", room.id);
  url.searchParams.set("identity", room.viewer_identity_id);
  const popup = window.open(url.href, `colorless-chat-${key}`, "popup,width=520,height=760");
  if (popup) windows.set(key, popup);
  else setAppStatus("팝업을 허용한 뒤 다시 눌러 주세요.");
}

export async function openInitialIdentityRoom(loadRoomsPage) {
  if (openedInitialRoom) return;
  const roomId = new URLSearchParams(window.location.search).get("chatRoom");
  if (!roomId) return;
  while (!state.roomById.has(roomId) && state.roomsNextCursor) await loadRoomsPage({ render: false });
  await openChatRoom(roomId);
  openedInitialRoom = true;
}
