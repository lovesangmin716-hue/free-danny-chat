"use strict";

export function createTicketListingFilter({
  searchButton,
  closeButton,
  form,
  dateInput,
  stadiumSelect,
  seatSelect,
  resetButton,
  statusNode,
  listingsNode,
  getDashboard,
  renderTicket,
  emptyCopy,
}) {
  function populateStadiumFilter() {
    const stadiums = getDashboard()?.stadiums || [];
    const selectedValue = stadiumSelect.value;
    stadiumSelect.replaceChildren(
      Object.assign(document.createElement("option"), { value: "", textContent: "전체 경기장" }),
      ...stadiums.map((stadium) => Object.assign(document.createElement("option"), {
        value: stadium,
        textContent: stadium,
      })),
    );
    stadiumSelect.value = stadiums.includes(selectedValue) ? selectedValue : "";
  }

  function syncSeatFilterOptions() {
    const dashboard = getDashboard();
    if (!dashboard) return;
    const stadium = stadiumSelect.value;
    const selectedValue = seatSelect.value;
    const grades = stadium ? dashboard.seat_grades?.[stadium] || [] : [];
    seatSelect.disabled = !stadium;
    seatSelect.replaceChildren(
      Object.assign(document.createElement("option"), {
        value: "",
        textContent: stadium ? "전체 좌석 등급" : "경기장을 먼저 선택",
      }),
      ...grades.map((grade) => Object.assign(document.createElement("option"), {
        value: grade,
        textContent: grade,
      })),
    );
    seatSelect.value = grades.includes(selectedValue) ? selectedValue : "";
  }

  function filteredListings() {
    const listings = getDashboard()?.listings || [];
    const date = dateInput.value;
    const stadium = stadiumSelect.value;
    const seatGrade = seatSelect.value;
    return listings.filter((ticket) => {
      if (date && ticket.game_date !== date) return false;
      if (stadium && ticket.stadium !== stadium) return false;
      return !seatGrade || ticket.seat_grade === seatGrade;
    });
  }

  function renderFilteredListings() {
    const listings = getDashboard()?.listings || [];
    const filtered = filteredListings();
    const hasFilters = Boolean(dateInput.value || stadiumSelect.value || seatSelect.value);
    statusNode.textContent = hasFilters
      ? `조건에 맞는 티켓 ${filtered.length}개 · 전체 ${listings.length}개`
      : `전체 티켓 ${listings.length}개`;
    listingsNode.replaceChildren(...(filtered.length
      ? filtered.map(renderTicket)
      : [emptyCopy(listings.length ? "조건에 맞는 티켓이 없습니다." : "현재 양도 중인 티켓이 없습니다.")]));
  }

  function resetListingFilters() {
    dateInput.value = "";
    stadiumSelect.value = "";
    seatSelect.value = "";
    syncSeatFilterOptions();
    renderFilteredListings();
  }

  function toggleListingSearch(forceOpen) {
    const shouldOpen = forceOpen ?? form.classList.contains("hidden");
    form.classList.toggle("hidden", !shouldOpen);
    searchButton.setAttribute("aria-expanded", String(shouldOpen));
    if (shouldOpen) window.setTimeout(() => dateInput.focus(), 0);
  }

  searchButton?.addEventListener("click", () => toggleListingSearch());
  closeButton?.addEventListener("click", () => toggleListingSearch(false));
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    renderFilteredListings();
    toggleListingSearch(false);
  });
  stadiumSelect?.addEventListener("change", syncSeatFilterOptions);
  resetButton?.addEventListener("click", resetListingFilters);

  return {
    configure() {
      populateStadiumFilter();
      syncSeatFilterOptions();
    },
    render: renderFilteredListings,
  };
}
