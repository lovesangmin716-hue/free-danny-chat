"use strict";

import { appScreen, getDisplayName, requestAction, setAppStatus, state } from "./core.js";
import { closeChatRoom, openChatRoom } from "./chat.js";
import { upsertMessengerRoom } from "./messenger.js";

const screen = document.getElementById("ticket-transfer-screen");
const openButton = document.getElementById("open-ticket-transfer-button");
const closeButton = document.getElementById("close-ticket-transfer-button");
const refreshButton = document.getElementById("refresh-ticket-transfer-button");
const identityLabel = document.getElementById("ticket-identity-label");
const identitySetup = document.getElementById("ticket-identity-setup");
const identitySelect = document.getElementById("ticket-identity-select");
const identityButton = document.getElementById("designate-ticket-identity-button");
const identityStatus = document.getElementById("ticket-identity-status");
const content = document.getElementById("ticket-transfer-content");
const screenStatus = document.getElementById("ticket-screen-status");
const listingForm = document.getElementById("ticket-listing-form");
const openFormButton = document.getElementById("open-ticket-form-button");
const cancelFormButton = document.getElementById("cancel-ticket-form-button");
const submitFormButton = document.getElementById("submit-ticket-form-button");
const formStatus = document.getElementById("ticket-form-status");
const gameDateInput = document.getElementById("ticket-game-date");
const stadiumSelect = document.getElementById("ticket-stadium");
const matchupInput = document.getElementById("ticket-matchup");
const seatInput = document.getElementById("ticket-seat");
const unitPriceInput = document.getElementById("ticket-unit-price");
const quantityInput = document.getElementById("ticket-quantity");
const deliverySelect = document.getElementById("ticket-delivery-method");
const descriptionInput = document.getElementById("ticket-description");
const listingsNode = document.getElementById("ticket-listings");
const sellingNode = document.getElementById("ticket-selling");
const dealsNode = document.getElementById("ticket-deals");

const ticketState = { dashboard: null, activeTab: "listings", loading: false, timer: null };
const won = new Intl.NumberFormat("ko-KR");

function localIsoDate(date = new Date()) {
  const offsetDate = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return offsetDate.toISOString().slice(0, 10);
}

function displayDate(value) {
  if (!value) return "날짜 미정";
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat("ko-KR", {
    month: "long", day: "numeric", weekday: "short",
  }).format(parsed);
}

function emptyCopy(text) {
  const node = document.createElement("p");
  node.className = "ticket-empty";
  node.textContent = text;
  return node;
}

function actionButton(label, action, className = "secondary-button") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

function ticketCard(ticket, { selling = false } = {}) {
  const card = document.createElement("article");
  card.className = "ticket-card";
  const heading = document.createElement("div");
  heading.className = "ticket-card-heading";
  const title = document.createElement("strong");
  title.textContent = `${displayDate(ticket.game_date)} · ${ticket.stadium}`;
  const badge = document.createElement("span");
  badge.className = "ticket-badge";
  badge.textContent = ticket.status === "sold_out" ? "양도 완료" : `${ticket.remaining_quantity}/${ticket.quantity}장`;
  heading.append(title, badge);

  const meta = document.createElement("div");
  meta.className = "ticket-card-meta";
  const matchup = ticket.matchup ? `${ticket.matchup} · ` : "";
  meta.append(
    Object.assign(document.createElement("span"), { textContent: `${matchup}${ticket.seat}` }),
    Object.assign(document.createElement("span"), { textContent: `${ticket.delivery_method} · 판매자 ${getDisplayName(ticket.seller || {})}` }),
    Object.assign(document.createElement("span"), { textContent: `경기일 +7일까지 대화와 거래 기록이 유지됩니다.` }),
  );
  const price = document.createElement("div");
  price.className = "ticket-card-price";
  price.textContent = `1장 ${won.format(Number(ticket.unit_price || 0))}원`;
  card.append(heading, meta, price);
  if (ticket.description) {
    const description = document.createElement("p");
    description.className = "ticket-card-description";
    description.textContent = ticket.description;
    card.appendChild(description);
  }
  const actions = document.createElement("div");
  actions.className = "ticket-card-actions";
  actions.appendChild(actionButton(selling ? "오픈채팅 보기" : "오픈채팅 참여", () => openTicketRoom(ticket.room)));
  card.appendChild(actions);

  if (selling) {
    const commenters = document.createElement("div");
    commenters.className = "ticket-commenters";
    const label = document.createElement("strong");
    label.textContent = `구매 희망자 ${ticket.commenters?.length || 0}명`;
    commenters.appendChild(label);
    if (!ticket.commenters?.length) commenters.appendChild(emptyCopy("오픈채팅에 메시지를 남긴 구매자가 아직 없습니다."));
    for (const buyer of ticket.commenters || []) {
      const row = document.createElement("div");
      row.className = "ticket-commenter";
      const name = document.createElement("span");
      name.textContent = `${getDisplayName(buyer)} (@${buyer.username})`;
      const quantity = document.createElement("input");
      quantity.type = "number";
      quantity.min = "1";
      quantity.max = String(Math.max(1, Number(ticket.remaining_quantity || 1)));
      quantity.value = "1";
      quantity.setAttribute("aria-label", `${getDisplayName(buyer)} 거래 수량`);
      const dealButton = actionButton("1:1 거래", () => openDeal(ticket.id, buyer.id, Number(quantity.value)), "");
      dealButton.disabled = ticket.status !== "open" || Number(ticket.remaining_quantity || 0) < 1;
      row.append(name, quantity, dealButton);
      commenters.appendChild(row);
    }
    card.appendChild(commenters);
  }
  return card;
}

function dealCard(deal) {
  const card = document.createElement("article");
  card.className = "ticket-card";
  const heading = document.createElement("div");
  heading.className = "ticket-card-heading";
  const title = document.createElement("strong");
  title.textContent = `${displayDate(deal.game_date)} · ${deal.stadium}`;
  const badge = document.createElement("span");
  badge.className = "ticket-badge";
  badge.textContent = deal.status === "completed" ? "거래 완료" : "거래 중";
  heading.append(title, badge);
  const counterpart = deal.role === "seller" ? deal.buyer : deal.seller;
  const meta = document.createElement("div");
  meta.className = "ticket-card-meta";
  meta.append(
    Object.assign(document.createElement("span"), { textContent: `${deal.seat} · ${deal.quantity}장` }),
    Object.assign(document.createElement("span"), { textContent: `${deal.role === "seller" ? "구매자" : "판매자"} ${getDisplayName(counterpart || {})} (@${counterpart?.username || "-"})` }),
    Object.assign(document.createElement("span"), { textContent: `${deal.delivery_method} · 총 ${won.format(Number(deal.unit_price || 0) * Number(deal.quantity || 0))}원` }),
  );
  const actions = document.createElement("div");
  actions.className = "ticket-card-actions";
  actions.appendChild(actionButton("개인 채팅", () => openTicketRoom(deal.room)));
  if (deal.role === "seller" && deal.status === "pending") {
    actions.appendChild(actionButton("거래 완료", () => completeDeal(deal.id), ""));
  }
  card.append(heading, meta, actions);
  return card;
}

function populateSelect(select, values) {
  select.replaceChildren(...values.map((value) => Object.assign(document.createElement("option"), {
    value, textContent: value,
  })));
}

function renderDashboard() {
  const dashboard = ticketState.dashboard;
  if (!dashboard) return;
  const baseballIdentity = dashboard.baseball_identity;
  identityLabel.textContent = baseballIdentity
    ? `${getDisplayName(baseballIdentity)} (@${baseballIdentity.username})`
    : "야구 전용 ID를 선택해 주세요.";
  identitySetup.classList.toggle("hidden", dashboard.active_identity_matches);
  content.classList.toggle("hidden", !dashboard.active_identity_matches);

  if (!baseballIdentity) {
    const identities = state.session?.identities || [state.session?.user].filter(Boolean);
    identitySelect.disabled = false;
    identitySelect.replaceChildren(...identities.map((identity) => Object.assign(document.createElement("option"), {
      value: identity.id,
      textContent: `${getDisplayName(identity)} (@${identity.username})`,
    })));
    identityButton.textContent = "이 ID를 야구 전용으로 선택";
  } else if (!dashboard.active_identity_matches) {
    identitySelect.replaceChildren(Object.assign(document.createElement("option"), {
      value: baseballIdentity.id,
      textContent: `${getDisplayName(baseballIdentity)} (@${baseballIdentity.username})`,
    }));
    identitySelect.disabled = true;
    identityButton.textContent = "야구 전용 ID로 전환";
  }

  populateSelect(stadiumSelect, dashboard.stadiums || []);
  populateSelect(deliverySelect, dashboard.delivery_methods || []);
  listingsNode.replaceChildren(...(dashboard.listings?.length
    ? dashboard.listings.map((ticket) => ticketCard(ticket))
    : [emptyCopy("현재 양도 중인 티켓이 없습니다.")]));
  sellingNode.replaceChildren(...(dashboard.selling?.length
    ? dashboard.selling.map((ticket) => ticketCard(ticket, { selling: true }))
    : [emptyCopy("내가 올린 티켓이 없습니다.")]));
  dealsNode.replaceChildren(...(dashboard.deals?.length
    ? dashboard.deals.map(dealCard)
    : [emptyCopy("진행 중이거나 완료된 거래가 없습니다.")]));
  setTicketTab(ticketState.activeTab);
}

async function loadTicketDashboard({ quiet = false } = {}) {
  if (ticketState.loading) return;
  ticketState.loading = true;
  refreshButton.disabled = true;
  if (!quiet) identityStatus.textContent = "티켓 정보를 불러오고 있어요.";
  try {
    ticketState.dashboard = await requestAction("tickets.dashboard", "/tickets", { headers: {} }, {
      key: "tickets.dashboard", policy: "join",
    });
    identityStatus.textContent = "";
    renderDashboard();
  } catch (error) {
    identityStatus.textContent = error.message;
    setAppStatus(error.message, "error");
  } finally {
    ticketState.loading = false;
    refreshButton.disabled = false;
  }
}

function openTicketTransfer() {
  if (!state.session?.user) return;
  screen.classList.remove("hidden");
  screen.setAttribute("aria-hidden", "false");
  appScreen.classList.add("ticket-transfer-active");
  gameDateInput.min = localIsoDate();
  if (!gameDateInput.value) gameDateInput.value = localIsoDate();
  void loadTicketDashboard();
  window.clearInterval(ticketState.timer);
  ticketState.timer = window.setInterval(() => void loadTicketDashboard({ quiet: true }), 20000);
}

function closeTicketTransfer() {
  if (state.selectedRoomId?.startsWith("room_")) closeChatRoom();
  window.clearInterval(ticketState.timer);
  ticketState.timer = null;
  screen.classList.add("hidden");
  screen.setAttribute("aria-hidden", "true");
  appScreen.classList.remove("ticket-transfer-active");
}

function setTicketTab(tab) {
  ticketState.activeTab = tab;
  screen.querySelectorAll("[data-ticket-tab]").forEach((button) => button.classList.toggle("active", button.dataset.ticketTab === tab));
  screen.querySelectorAll("[data-ticket-pane]").forEach((pane) => pane.classList.toggle("hidden", pane.dataset.ticketPane !== tab));
}

async function designateOrSwitchIdentity() {
  const identityId = identitySelect.value;
  if (!identityId) return;
  identityButton.disabled = true;
  identityStatus.textContent = "야구 전용 ID를 설정하고 있어요.";
  try {
    if (!ticketState.dashboard?.baseball_identity) {
      await requestAction("tickets.identity", "/tickets/identity", {
        method: "POST", body: JSON.stringify({ identityId }),
      });
    }
    if (identityId !== state.session?.active_identity_id) {
      await requestAction("tickets.identity-switch", "/identities/switch", {
        method: "POST", body: JSON.stringify({ identityId }),
      });
    }
    window.location.reload();
  } catch (error) {
    identityStatus.textContent = error.message;
    identityButton.disabled = false;
  }
}

async function submitListing(event) {
  event.preventDefault();
  submitFormButton.disabled = true;
  formStatus.textContent = "티켓을 등록하고 있어요.";
  try {
    await requestAction("tickets.create", "/tickets", {
      method: "POST",
      body: JSON.stringify({
        gameDate: gameDateInput.value,
        stadium: stadiumSelect.value,
        matchup: matchupInput.value.trim(),
        seat: seatInput.value.trim(),
        unitPrice: Number(unitPriceInput.value),
        quantity: Number(quantityInput.value),
        deliveryMethod: deliverySelect.value,
        description: descriptionInput.value.trim(),
      }),
    });
    listingForm.reset();
    gameDateInput.value = localIsoDate();
    quantityInput.value = "1";
    listingForm.classList.add("hidden");
    formStatus.textContent = "";
    await loadTicketDashboard();
  } catch (error) {
    formStatus.textContent = error.message;
  } finally {
    submitFormButton.disabled = false;
  }
}

async function openTicketRoom(room) {
  if (!room?.id) return;
  try {
    upsertMessengerRoom(room);
    await openChatRoom(room.id);
  } catch (error) {
    screenStatus.textContent = error.message;
  }
}

async function openDeal(listingId, buyerUserId, quantity) {
  try {
    const payload = await requestAction("tickets.deal", "/tickets/deals", {
      method: "POST", body: JSON.stringify({ listingId, buyerUserId, quantity }),
    });
    await openTicketRoom(payload.deal?.room);
    await loadTicketDashboard({ quiet: true });
  } catch (error) {
    screenStatus.textContent = error.message;
  }
}

async function completeDeal(dealRoomId) {
  try {
    await requestAction("tickets.complete", "/tickets/deals/complete", {
      method: "POST", body: JSON.stringify({ dealRoomId }),
    });
    screenStatus.textContent = "거래 완료로 표시했습니다.";
    await loadTicketDashboard();
  } catch (error) {
    screenStatus.textContent = error.message;
  }
}

openButton?.addEventListener("click", openTicketTransfer);
closeButton?.addEventListener("click", closeTicketTransfer);
refreshButton?.addEventListener("click", () => void loadTicketDashboard());
identityButton?.addEventListener("click", () => void designateOrSwitchIdentity());
openFormButton?.addEventListener("click", () => listingForm.classList.remove("hidden"));
cancelFormButton?.addEventListener("click", () => listingForm.classList.add("hidden"));
listingForm?.addEventListener("submit", (event) => void submitListing(event));
screen?.querySelectorAll("[data-ticket-tab]").forEach((button) => button.addEventListener("click", () => setTicketTab(button.dataset.ticketTab)));

export { closeTicketTransfer, loadTicketDashboard, openTicketTransfer };
