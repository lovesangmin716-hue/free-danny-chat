"use strict";

import { appScreen, getDisplayName, requestAction, setAppStatus, state } from "./core.js";
import { closeChatRoom, openChatRoom } from "./chat.js";
import { upsertMessengerRoom } from "./messenger.js";

const screen = document.getElementById("ticket-transfer-screen");
const openButton = document.getElementById("open-ticket-transfer-button");
const adminOpenButton = document.getElementById("open-ticket-admin-button");
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
const homeTeamInput = document.getElementById("ticket-home-team");
const awayTeamSelect = document.getElementById("ticket-away-team");
const seatGradeSelectField = document.getElementById("ticket-seat-grade-select-field");
const seatGradeSelect = document.getElementById("ticket-seat-grade-select");
const seatGradeInputField = document.getElementById("ticket-seat-grade-input-field");
const seatGradeInput = document.getElementById("ticket-seat-grade-input");
const seatDetailInput = document.getElementById("ticket-seat-detail");
const purchasePriceInput = document.getElementById("ticket-purchase-price");
const unitPriceInput = document.getElementById("ticket-unit-price");
const quantityInput = document.getElementById("ticket-quantity");
const deliverySelect = document.getElementById("ticket-delivery-method");
const descriptionInput = document.getElementById("ticket-description");
const listingsNode = document.getElementById("ticket-listings");
const listingFilters = document.getElementById("ticket-listing-filters");
const filterDateInput = document.getElementById("ticket-filter-date");
const filterStadiumSelect = document.getElementById("ticket-filter-stadium");
const filterSeatInput = document.getElementById("ticket-filter-seat");
const filterSeatOptions = document.getElementById("ticket-filter-seat-options");
const filterResetButton = document.getElementById("reset-ticket-filters");
const filterStatus = document.getElementById("ticket-filter-status");
const sellingNode = document.getElementById("ticket-selling");
const dealsNode = document.getElementById("ticket-deals");
const accessPanel = document.getElementById("ticket-access-panel");
const accessTitle = document.getElementById("ticket-access-title");
const accessCopy = document.getElementById("ticket-access-copy");
const accessActions = document.getElementById("ticket-access-actions");
const moderationTab = document.getElementById("ticket-moderation-tab");
const moderationNode = document.getElementById("ticket-moderation");
const agreementModal = document.getElementById("ticket-agreement-modal");
const agreementForm = document.getElementById("ticket-agreement-form");
const agreementSignature = document.getElementById("ticket-agreement-signature");
const clearAgreementSignatureButton = document.getElementById("clear-ticket-agreement-signature");
const agreementSubmit = document.getElementById("submit-ticket-agreement-button");
const agreementStatus = document.getElementById("ticket-agreement-status");
const closeAgreementButton = document.getElementById("close-ticket-agreement-button");
const reportModal = document.getElementById("ticket-report-modal");
const reportForm = document.getElementById("ticket-report-form");
const reportReason = document.getElementById("ticket-report-reason");
const reportDescription = document.getElementById("ticket-report-description");
const reportPriceNotice = document.getElementById("ticket-report-price-notice");
const reportSubmit = document.getElementById("submit-ticket-report-button");
const reportStatus = document.getElementById("ticket-report-status");
const closeReportButton = document.getElementById("close-ticket-report-button");

const ticketState = {
  dashboard: null,
  activeTab: "listings",
  loading: false,
  timer: null,
  reportListingId: "",
  pendingListingPayload: null,
  signatureDrawing: false,
  signatureDrawn: false,
};
const won = new Intl.NumberFormat("ko-KR");

function signatureContext() {
  return agreementSignature?.getContext("2d", { alpha: false }) || null;
}

function clearAgreementSignature() {
  const context = signatureContext();
  if (!context) return;
  context.fillStyle = "#fff";
  context.fillRect(0, 0, agreementSignature.width, agreementSignature.height);
  context.strokeStyle = "#000";
  context.lineCap = "round";
  context.lineJoin = "round";
  context.lineWidth = 6;
  ticketState.signatureDrawing = false;
  ticketState.signatureDrawn = false;
}

function signaturePoint(event) {
  const bounds = agreementSignature.getBoundingClientRect();
  return {
    x: (event.clientX - bounds.left) * (agreementSignature.width / Math.max(1, bounds.width)),
    y: (event.clientY - bounds.top) * (agreementSignature.height / Math.max(1, bounds.height)),
  };
}

function startSignature(event) {
  event.preventDefault();
  const context = signatureContext();
  if (!context) return;
  const point = signaturePoint(event);
  ticketState.signatureDrawing = true;
  ticketState.signatureDrawn = true;
  agreementSignature.setPointerCapture?.(event.pointerId);
  context.beginPath();
  context.moveTo(point.x, point.y);
  context.lineTo(point.x + .1, point.y + .1);
  context.stroke();
}

function moveSignature(event) {
  if (!ticketState.signatureDrawing) return;
  event.preventDefault();
  const context = signatureContext();
  const point = signaturePoint(event);
  context.lineTo(point.x, point.y);
  context.stroke();
}

function stopSignature(event) {
  if (!ticketState.signatureDrawing) return;
  ticketState.signatureDrawing = false;
  if (agreementSignature.hasPointerCapture?.(event.pointerId)) agreementSignature.releasePointerCapture(event.pointerId);
}

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

function verifiedBadge(user) {
  if (!user?.ticket_verified) return null;
  const badge = document.createElement("span");
  badge.className = "ticket-verified";
  badge.textContent = "✓";
  badge.title = "@itsyou가 검증한 티켓 계정";
  badge.setAttribute("aria-label", "검증된 티켓 계정");
  return badge;
}

function appendUserName(node, user, { handle = false } = {}) {
  node.append(document.createTextNode(`${getDisplayName(user || {})}${handle ? ` (@${user?.username || "-"})` : ""}`));
  const badge = verifiedBadge(user);
  if (badge) node.appendChild(badge);
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
  badge.textContent = ticket.status === "sold_out"
    ? "양도 완료"
    : ticket.status === "suspended" ? "판매 정지" : `${ticket.remaining_quantity}/${ticket.quantity}장`;
  heading.append(title, badge);

  const meta = document.createElement("div");
  meta.className = "ticket-card-meta";
  const matchup = ticket.matchup || [ticket.home_team, ticket.away_team].filter(Boolean).join(" vs ");
  const seat = ticket.seat_grade
    ? `${ticket.seat_grade} · ${ticket.seat_detail || "구역·열 정보 없음"}`
    : ticket.seat;
  const sellerLine = document.createElement("span");
  sellerLine.append(document.createTextNode(`${ticket.delivery_method} · 판매자 `));
  appendUserName(sellerLine, ticket.seller || {});
  meta.append(
    Object.assign(document.createElement("span"), { textContent: matchup || "경기 정보 없음" }),
    Object.assign(document.createElement("span"), { textContent: seat || "좌석 정보 없음" }),
    sellerLine,
  );
  const price = document.createElement("div");
  price.className = "ticket-card-price";
  const purchasePrice = Number(ticket.purchase_price ?? ticket.unit_price ?? 0);
  price.append(
    Object.assign(document.createElement("span"), {
      className: "ticket-card-purchase-price",
      textContent: `구매가 ${won.format(purchasePrice)}원`,
    }),
    Object.assign(document.createElement("strong"), {
      className: "ticket-card-sale-price",
      textContent: `판매가 ${won.format(Number(ticket.unit_price || 0))}원`,
    }),
  );
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
  if (!selling) {
    actions.appendChild(actionButton("! 신고", () => openReportModal(ticket), "secondary-button ticket-report-button"));
  }
  if (selling) {
    actions.appendChild(actionButton("글 삭제", () => deleteListing(ticket.id), "secondary-button ticket-delete-button"));
  }
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
      appendUserName(name, buyer, { handle: true });
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
  const matchup = deal.matchup || [deal.home_team, deal.away_team].filter(Boolean).join(" vs ");
  const seat = deal.seat_grade
    ? `${deal.seat_grade} · ${deal.seat_detail || "구역·열 정보 없음"}`
    : deal.seat;
  const counterpartLine = document.createElement("span");
  counterpartLine.append(document.createTextNode(`${deal.role === "seller" ? "구매자" : "판매자"} `));
  appendUserName(counterpartLine, counterpart || {}, { handle: true });
  meta.append(
    Object.assign(document.createElement("span"), { textContent: matchup || "경기 정보 없음" }),
    Object.assign(document.createElement("span"), { textContent: `${seat || "좌석 정보 없음"} · ${deal.quantity}장` }),
    counterpartLine,
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

function populateSelect(select, values, selectedValue = select.value) {
  select.replaceChildren(...values.map((value) => Object.assign(document.createElement("option"), {
    value, textContent: value,
  })));
  if (values.includes(selectedValue)) select.value = selectedValue;
}

function populateStadiumFilter() {
  const stadiums = ticketState.dashboard?.stadiums || [];
  const selectedValue = filterStadiumSelect.value;
  filterStadiumSelect.replaceChildren(
    Object.assign(document.createElement("option"), { value: "", textContent: "전체 경기장" }),
    ...stadiums.map((stadium) => Object.assign(document.createElement("option"), {
      value: stadium, textContent: stadium,
    })),
  );
  filterStadiumSelect.value = stadiums.includes(selectedValue) ? selectedValue : "";
}

function syncSeatFilterOptions() {
  const dashboard = ticketState.dashboard;
  if (!dashboard) return;
  const stadium = filterStadiumSelect.value;
  const grades = stadium
    ? dashboard.seat_grades?.[stadium] || []
    : [...new Set(Object.values(dashboard.seat_grades || {}).flat())].sort((left, right) => left.localeCompare(right, "ko"));
  filterSeatOptions.replaceChildren(...grades.map((grade) => Object.assign(document.createElement("option"), {
    value: grade,
  })));
}

function normalizedSeatText(value) {
  return String(value || "").toLocaleLowerCase("ko-KR").replace(/\s+/g, "");
}

function filteredListings() {
  const listings = ticketState.dashboard?.listings || [];
  const date = filterDateInput.value;
  const stadium = filterStadiumSelect.value;
  const seatQuery = normalizedSeatText(filterSeatInput.value);
  return listings.filter((ticket) => {
    if (date && ticket.game_date !== date) return false;
    if (stadium && ticket.stadium !== stadium) return false;
    if (!seatQuery) return true;
    const seatText = normalizedSeatText([ticket.seat_grade, ticket.seat_detail, ticket.seat].filter(Boolean).join(" "));
    return seatText.includes(seatQuery);
  });
}

function renderFilteredListings() {
  const listings = ticketState.dashboard?.listings || [];
  const filtered = filteredListings();
  const hasFilters = Boolean(filterDateInput.value || filterStadiumSelect.value || filterSeatInput.value.trim());
  filterStatus.textContent = hasFilters
    ? `조건에 맞는 티켓 ${filtered.length}개 · 전체 ${listings.length}개`
    : `전체 티켓 ${listings.length}개`;
  listingsNode.replaceChildren(...(filtered.length
    ? filtered.map((ticket) => ticketCard(ticket))
    : [emptyCopy(listings.length ? "조건에 맞는 티켓이 없습니다." : "현재 양도 중인 티켓이 없습니다.")]));
}

function resetListingFilters() {
  filterDateInput.value = "";
  filterStadiumSelect.value = "";
  filterSeatInput.value = "";
  syncSeatFilterOptions();
  renderFilteredListings();
}

function syncStadiumFields() {
  const dashboard = ticketState.dashboard;
  if (!dashboard) return;
  const stadium = stadiumSelect.value;
  const homeTeam = dashboard.home_teams?.[stadium] || "";
  homeTeamInput.value = homeTeam;
  populateSelect(awayTeamSelect, (dashboard.teams || []).filter((team) => team !== homeTeam));

  const grades = dashboard.seat_grades?.[stadium] || [];
  const hasConfiguredGrades = grades.length > 0;
  seatGradeSelectField.classList.toggle("hidden", !hasConfiguredGrades);
  seatGradeInputField.classList.toggle("hidden", hasConfiguredGrades);
  seatGradeSelect.required = hasConfiguredGrades;
  seatGradeInput.required = !hasConfiguredGrades;
  if (hasConfiguredGrades) populateSelect(seatGradeSelect, grades);
}

function showAgreementModal() {
  agreementStatus.textContent = "";
  agreementForm.reset();
  clearAgreementSignature();
  agreementModal.classList.remove("hidden");
  window.setTimeout(() => agreementSignature.focus(), 0);
}

function closeAgreementModal() {
  agreementModal.classList.add("hidden");
  ticketState.pendingListingPayload = null;
}

function renderAccessPanel() {
  const dashboard = ticketState.dashboard;
  const access = dashboard?.ticket_access || {};
  accessActions.replaceChildren();
  if (dashboard?.is_admin) {
    accessPanel.classList.add("hidden");
    return;
  }
  accessPanel.classList.remove("hidden");
  if (access.status === "suspended") {
    accessTitle.textContent = "티켓 기능이 정지되었습니다";
    accessCopy.textContent = `${access.suspension?.reason_label || "신고 접수"} 사유로 판매 게시물이 즉시 중지되었습니다. 관리자 @itsyou에게 소명해 주세요.`;
    if (dashboard.admin_contact_available) accessActions.appendChild(actionButton("@itsyou에게 소명하기", openAdminChat));
    return;
  }
  if (access.verified) {
    accessTitle.replaceChildren(document.createTextNode("검증된 티켓 계정"), verifiedBadge({ ticket_verified: true }));
    accessCopy.textContent = `@itsyou 검증이 완료되었습니다. 판매자 이름 옆에 레드체크가 표시됩니다.`;
  } else if (access.verification_status === "pending") {
    accessTitle.textContent = "레드체크 검증 대기 중";
    accessCopy.textContent = "@itsyou가 요청을 검토하고 있습니다. 필요한 자료는 관리자 채팅으로 전달해 주세요.";
  } else {
    accessTitle.textContent = "티켓 판매 계정";
    accessCopy.textContent = access.has_signed_listing
      ? "판매글마다 화면에 직접 서명해야 합니다. @itsyou에 검증을 요청하면 승인 후 레드체크가 표시됩니다."
      : "판매글을 등록할 때마다 화면에 직접 서명해야 합니다. 첫 판매글 등록 후 @itsyou에 레드체크 검증을 요청할 수 있습니다.";
    if (access.has_signed_listing) accessActions.appendChild(actionButton("레드체크 검증 요청", requestVerification, ""));
  }
  if (dashboard.admin_contact_available) accessActions.appendChild(actionButton("@itsyou 관리자 채팅", openAdminChat));
}

function moderationItem(title, lines, actions = [], extras = []) {
  const item = document.createElement("div");
  item.className = "ticket-admin-item";
  item.appendChild(Object.assign(document.createElement("strong"), { textContent: title }));
  for (const line of lines.filter(Boolean)) item.appendChild(Object.assign(document.createElement("p"), { textContent: line }));
  item.append(...extras);
  if (actions.length) {
    const actionRow = document.createElement("div");
    actionRow.className = "ticket-card-actions";
    actionRow.append(...actions);
    item.appendChild(actionRow);
  }
  return item;
}

function moderationSection(title, items, emptyText) {
  const section = document.createElement("section");
  section.className = "ticket-admin-section";
  section.appendChild(Object.assign(document.createElement("h4"), { textContent: title }));
  section.append(...(items.length ? items : [emptyCopy(emptyText)]));
  return section;
}

function renderModeration() {
  const moderation = ticketState.dashboard?.moderation || {};
  if (!ticketState.dashboard?.is_admin) {
    moderationNode.replaceChildren();
    return;
  }
  const requests = (moderation.verification_requests || []).map((request) => {
    const actions = request.status === "pending"
      ? [
        actionButton("레드체크 승인", () => adminAction(request.user.id, "verify"), ""),
        actionButton("요청 거절", () => adminAction(request.user.id, "reject_verification")),
      ]
      : request.user.ticket_verified
        ? [actionButton("레드체크 해제", () => adminAction(request.user.id, "unverify"))]
        : [actionButton("레드체크 부여", () => adminAction(request.user.id, "verify"), "")];
    return moderationItem(
      `${getDisplayName(request.user)} (@${request.user.username}) · ${request.status}`,
      [request.note || "요청 메모 없음", `요청: ${request.requested_at || "-"}`, request.reviewed_at ? `처리: ${request.reviewed_at}` : ""],
      actions,
    );
  });
  const reports = (moderation.reports || []).map((report) => {
    const actions = report.status === "open" && report.seller ? [
      actionButton("소명 확인·정지 해제", () => adminAction(report.seller.id, "reinstate", report.id), ""),
      actionButton("신고 확인·정지 유지", () => adminAction(report.seller.id, "resolve_report", report.id)),
    ] : [];
    return moderationItem(
      `${report.reason_label} · ${report.status}`,
      [
        `판매자 @${report.seller?.username || "-"} / 신고자 @${report.reporter?.username || "-"}`,
        `${report.listing?.stadium || ""} ${report.listing?.seat || ""}`,
        `판매가 ${won.format(Number(report.listing?.unit_price || 0))}원 / 구매가 ${won.format(Number(report.listing?.purchase_price || 0))}원`,
        report.description || "상세 내용 없음",
        report.created_at || "",
      ],
      actions,
    );
  });
  const agreements = (moderation.agreements || []).map((agreement) => {
    const signature = document.createElement("img");
    signature.className = "ticket-admin-signature";
    signature.src = agreement.image_data_url;
    signature.alt = `@${agreement.user.username} 판매 서명`;
    return moderationItem(
      `${getDisplayName(agreement.user)} (@${agreement.user.username})`,
      [
        `${agreement.listing?.game_date || ""} · ${agreement.listing?.stadium || ""} · ${agreement.listing?.seat || ""}`,
        `구매가 ${won.format(Number(agreement.listing?.purchase_price || 0))}원 / 판매가 ${won.format(Number(agreement.listing?.unit_price || 0))}원`,
        `서명 시각: ${agreement.signed_at}`,
      ],
      [],
      [signature],
    );
  });
  const suspended = (moderation.suspended_users || []).map((entry) => moderationItem(
    `${getDisplayName(entry.user)} (@${entry.user.username})`,
    [entry.suspension?.reason_label || "정지", entry.suspension?.suspended_at || ""],
    [actionButton("정지 해제", () => adminAction(entry.user.id, "reinstate"), "")],
  ));
  moderationNode.replaceChildren(
    moderationSection("레드체크 검증 요청", requests, "대기 중인 검증 요청이 없습니다."),
    moderationSection("신고", reports, "접수된 신고가 없습니다."),
    moderationSection("정지 계정", suspended, "정지된 티켓 계정이 없습니다."),
    moderationSection("판매 규정 서명", agreements, "저장된 서명이 없습니다."),
  );
}

function renderDashboard() {
  const dashboard = ticketState.dashboard;
  if (!dashboard) return;
  const baseballIdentity = dashboard.baseball_identity;
  identityLabel.textContent = baseballIdentity
    ? `${getDisplayName(baseballIdentity)} (@${baseballIdentity.username})`
    : dashboard.is_admin ? `${getDisplayName(state.session?.user)} 티켓 관리자` : "야구 전용 ID를 선택해 주세요.";
  identitySetup.classList.toggle("hidden", dashboard.active_identity_matches || dashboard.is_admin);
  content.classList.toggle("hidden", !dashboard.active_identity_matches && !dashboard.is_admin);
  moderationTab.classList.toggle("hidden", !dashboard.is_admin);
  if (dashboard.is_admin && ticketState.activeTab === "listings") {
    ticketState.activeTab = "moderation";
  }

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
  populateStadiumFilter();
  syncSeatFilterOptions();
  syncStadiumFields();
  renderAccessPanel();
  renderFilteredListings();
  sellingNode.replaceChildren(...(dashboard.selling?.length
    ? dashboard.selling.map((ticket) => ticketCard(ticket, { selling: true }))
    : [emptyCopy("내가 올린 티켓이 없습니다.")]));
  dealsNode.replaceChildren(...(dashboard.deals?.length
    ? dashboard.deals.map(dealCard)
    : [emptyCopy("진행 중이거나 완료된 거래가 없습니다.")]));
  renderModeration();
  openFormButton.disabled = dashboard.is_admin || dashboard.ticket_access?.status === "suspended";
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

function openTicketAdmin() {
  ticketState.activeTab = "moderation";
  openTicketTransfer();
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

async function signAgreement(event) {
  event.preventDefault();
  if (!ticketState.pendingListingPayload) {
    agreementStatus.textContent = "등록할 판매글 정보가 없습니다. 판매 폼부터 작성해 주세요.";
    return;
  }
  if (!ticketState.signatureDrawn) {
    agreementStatus.textContent = "서명란에 직접 서명해 주세요.";
    return;
  }
  agreementSubmit.disabled = true;
  agreementStatus.textContent = "서명과 판매글을 저장하고 있어요.";
  try {
    await requestAction("tickets.create", "/tickets", {
      method: "POST",
      body: JSON.stringify({
        ...ticketState.pendingListingPayload,
        signatureImage: agreementSignature.toDataURL("image/png"),
      }),
    });
    agreementModal.classList.add("hidden");
    ticketState.pendingListingPayload = null;
    agreementForm.reset();
    listingForm.reset();
    gameDateInput.value = localIsoDate();
    quantityInput.value = "1";
    syncStadiumFields();
    listingForm.classList.add("hidden");
    formStatus.textContent = "";
    screenStatus.textContent = "판매글과 손그림 서명이 @itsyou 관리자 기록에 저장되었습니다.";
    await loadTicketDashboard();
  } catch (error) {
    agreementStatus.textContent = error.message;
  } finally {
    agreementSubmit.disabled = false;
  }
}

function openReportModal(ticket) {
  ticketState.reportListingId = ticket.id;
  reportForm.reset();
  reportStatus.textContent = "";
  populateSelect(reportReason, Object.keys(ticketState.dashboard?.report_reasons || {}));
  for (const option of reportReason.options) {
    option.textContent = ticketState.dashboard?.report_reasons?.[option.value] || option.value;
  }
  reportPriceNotice.textContent = ticketState.dashboard?.purchase_price_notice || "";
  reportModal.classList.remove("hidden");
  window.setTimeout(() => reportReason.focus(), 0);
}

function closeReportModal() {
  ticketState.reportListingId = "";
  reportModal.classList.add("hidden");
}

async function submitReport(event) {
  event.preventDefault();
  if (!ticketState.reportListingId) return;
  reportSubmit.disabled = true;
  reportStatus.textContent = "신고를 접수하고 있어요.";
  try {
    await requestAction("tickets.report", "/tickets/reports", {
      method: "POST",
      body: JSON.stringify({
        listingId: ticketState.reportListingId,
        reason: reportReason.value,
        description: reportDescription.value.trim(),
      }),
    });
    closeReportModal();
    screenStatus.textContent = "신고가 접수되어 피신고 판매자의 티켓 기능이 즉시 정지되었습니다.";
    await loadTicketDashboard({ quiet: true });
  } catch (error) {
    reportStatus.textContent = error.message;
  } finally {
    reportSubmit.disabled = false;
  }
}

async function requestVerification() {
  const note = window.prompt("@itsyou에게 전달할 검증 메모를 입력해 주세요. 필요한 자료는 관리자 채팅으로 보낼 수 있습니다.", "");
  if (note === null) return;
  try {
    await requestAction("tickets.verification", "/tickets/verification", {
      method: "POST", body: JSON.stringify({ note: note.trim() }),
    });
    screenStatus.textContent = "레드체크 검증을 @itsyou에 요청했습니다.";
    await loadTicketDashboard({ quiet: true });
  } catch (error) {
    screenStatus.textContent = error.message;
  }
}

async function openAdminChat() {
  try {
    const payload = await requestAction("tickets.admin-chat", "/tickets/admin-chat", {
      method: "POST", body: "{}",
    });
    await openTicketRoom(payload.room);
  } catch (error) {
    screenStatus.textContent = error.message;
  }
}

async function adminAction(userId, action, reportId = "") {
  try {
    await requestAction("tickets.admin-action", "/tickets/admin-action", {
      method: "POST", body: JSON.stringify({ userId, action, reportId }),
    });
    screenStatus.textContent = "관리자 처리가 완료되었습니다.";
    await loadTicketDashboard({ quiet: true });
  } catch (error) {
    screenStatus.textContent = error.message;
  }
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

function submitListing(event) {
  event.preventDefault();
  if (!ticketState.dashboard?.ticket_access?.can_sell) {
    formStatus.textContent = "티켓 판매 기능이 정지되어 있습니다. @itsyou에 소명해 주세요.";
    return;
  }
  if (Number(unitPriceInput.value) > Number(purchasePriceInput.value)) {
    formStatus.textContent = "판매가는 실제 구매가를 초과할 수 없습니다.";
    unitPriceInput.focus();
    return;
  }
  ticketState.pendingListingPayload = {
    gameDate: gameDateInput.value,
    stadium: stadiumSelect.value,
    awayTeam: awayTeamSelect.value,
    seatGrade: seatGradeSelectField.classList.contains("hidden")
      ? seatGradeInput.value.trim()
      : seatGradeSelect.value,
    seatDetail: seatDetailInput.value.trim(),
    purchasePrice: Number(purchasePriceInput.value),
    unitPrice: Number(unitPriceInput.value),
    quantity: Number(quantityInput.value),
    deliveryMethod: deliverySelect.value,
    description: descriptionInput.value.trim(),
  };
  formStatus.textContent = "마지막으로 화면에 직접 서명해 주세요.";
  showAgreementModal();
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

async function deleteListing(listingId) {
  if (!window.confirm("이 양도 글을 삭제할까요? 기존 1:1 거래 기록은 유지됩니다.")) return;
  try {
    await requestAction("tickets.delete", "/tickets/delete", {
      method: "POST", body: JSON.stringify({ listingId }),
    });
    screenStatus.textContent = "양도 글을 삭제했습니다.";
    await loadTicketDashboard();
  } catch (error) {
    screenStatus.textContent = error.message;
  }
}

openButton?.addEventListener("click", openTicketTransfer);
adminOpenButton?.addEventListener("click", openTicketAdmin);
closeButton?.addEventListener("click", closeTicketTransfer);
refreshButton?.addEventListener("click", () => void loadTicketDashboard());
identityButton?.addEventListener("click", () => void designateOrSwitchIdentity());
openFormButton?.addEventListener("click", () => {
  if (!ticketState.dashboard?.ticket_access?.can_sell) {
    screenStatus.textContent = "티켓 판매 기능이 정지되어 있습니다. @itsyou에 소명해 주세요.";
    return;
  }
  syncStadiumFields();
  listingForm.classList.remove("hidden");
});
cancelFormButton?.addEventListener("click", () => listingForm.classList.add("hidden"));
listingForm?.addEventListener("submit", (event) => void submitListing(event));
stadiumSelect?.addEventListener("change", syncStadiumFields);
listingFilters?.addEventListener("submit", (event) => event.preventDefault());
filterDateInput?.addEventListener("input", renderFilteredListings);
filterStadiumSelect?.addEventListener("change", () => {
  syncSeatFilterOptions();
  renderFilteredListings();
});
filterSeatInput?.addEventListener("input", renderFilteredListings);
filterResetButton?.addEventListener("click", resetListingFilters);
screen?.querySelectorAll("[data-ticket-tab]").forEach((button) => button.addEventListener("click", () => setTicketTab(button.dataset.ticketTab)));
agreementForm?.addEventListener("submit", (event) => void signAgreement(event));
closeAgreementButton?.addEventListener("click", closeAgreementModal);
agreementModal?.addEventListener("click", (event) => { if (event.target === agreementModal) closeAgreementModal(); });
clearAgreementSignatureButton?.addEventListener("click", clearAgreementSignature);
agreementSignature?.addEventListener("pointerdown", startSignature);
agreementSignature?.addEventListener("pointermove", moveSignature);
agreementSignature?.addEventListener("pointerup", stopSignature);
agreementSignature?.addEventListener("pointercancel", stopSignature);
agreementSignature?.addEventListener("lostpointercapture", () => { ticketState.signatureDrawing = false; });
reportForm?.addEventListener("submit", (event) => void submitReport(event));
closeReportButton?.addEventListener("click", closeReportModal);
reportModal?.addEventListener("click", (event) => { if (event.target === reportModal) closeReportModal(); });

export { closeTicketTransfer, loadTicketDashboard, openTicketTransfer };
