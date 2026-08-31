"use strict";

import { requestAction, state } from "./core.js";
import { closeChatRoom } from "./chat.js";
import { removeMessengerRoom } from "./messenger.js";

export function createTicketChatActions({ statusNode, reloadDashboard }) {
  async function cancelInterest(listingId) {
    if (!window.confirm("티켓 신청을 취소할까요? 기존 오픈채팅 대화는 그대로 유지됩니다.")) return;
    try {
      await requestAction("tickets.cancel", "/tickets/cancel", {
        method: "POST", body: JSON.stringify({ listingId }),
      });
      statusNode.textContent = "티켓 신청을 취소했습니다. 오픈채팅 대화는 유지됩니다.";
      await reloadDashboard({ quiet: true });
    } catch (error) {
      statusNode.textContent = error.message;
    }
  }

  async function leaveRoom(roomId, kind = "listing") {
    if (!roomId) return;
    const copy = kind === "listing"
      ? "오픈채팅에서 나갈까요? 신청도 함께 취소되며, 지금까지의 대화는 다른 참여자에게 그대로 남습니다."
      : "1:1 채팅에서 나갈까요? 지금까지의 대화는 상대방에게 그대로 남습니다.";
    if (!window.confirm(copy)) return;
    try {
      await requestAction("tickets.leave", "/tickets/leave", {
        method: "POST", body: JSON.stringify({ roomId }),
      });
      if (state.selectedRoomId === roomId) closeChatRoom();
      removeMessengerRoom(roomId);
      statusNode.textContent = "채팅에서 나갔습니다. 기존 대화 기록은 삭제되지 않습니다.";
      await reloadDashboard({ quiet: true });
    } catch (error) {
      statusNode.textContent = error.message;
    }
  }

  return Object.freeze({ cancelInterest, leaveRoom });
}
