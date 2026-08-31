"use strict";

export function renderTicketChats({
  dashboard,
  container,
  displayDate,
  actionButton,
  openRoom,
  emptyCopy,
}) {
  function chatOverviewCard(item) {
    const card = document.createElement("article");
    card.className = "ticket-card";
    const isDeal = item.chat_kind === "deal";
    const heading = document.createElement("div");
    heading.className = "ticket-card-heading";
    heading.append(
      Object.assign(document.createElement("strong"), {
        textContent: `${displayDate(item.game_date)} · ${item.stadium}`,
      }),
      Object.assign(document.createElement("span"), {
        className: "ticket-badge",
        textContent: isDeal ? "1:1 거래" : "오픈채팅",
      }),
    );
    const meta = document.createElement("div");
    meta.className = "ticket-card-meta";
    meta.append(
      Object.assign(document.createElement("span"), { textContent: item.matchup || "경기 정보 없음" }),
      Object.assign(document.createElement("span"), { textContent: item.seat || "좌석 정보 없음" }),
    );
    if (!isDeal && Number(item.viewer_interest_quantity || 0) > 0) {
      meta.appendChild(Object.assign(document.createElement("span"), {
        textContent: `내 희망 수량 ${item.viewer_interest_quantity}장`,
      }));
    }
    const actions = document.createElement("div");
    actions.className = "ticket-card-actions";
    actions.appendChild(actionButton(isDeal ? "개인 채팅 열기" : "오픈채팅 열기", () => openRoom(item.room), ""));
    card.append(heading, meta, actions);
    return card;
  }

  const chatsByRoom = new Map();
  for (const ticket of [...(dashboard.selling || []), ...(dashboard.buying || [])]) {
    if (ticket.room?.id) chatsByRoom.set(ticket.room.id, { ...ticket, chat_kind: "listing" });
  }
  for (const deal of dashboard.deals || []) {
    if (deal.room?.id) chatsByRoom.set(deal.room.id, { ...deal, chat_kind: "deal" });
  }
  const chats = [...chatsByRoom.values()].sort((left, right) => String(
    right.room?.updated_at || right.updated_at || right.created_at || "",
  ).localeCompare(String(left.room?.updated_at || left.updated_at || left.created_at || "")));
  container.replaceChildren(...(chats.length
    ? chats.map(chatOverviewCard)
    : [emptyCopy("참여 중인 티켓 채팅이 없습니다.")]));
}
