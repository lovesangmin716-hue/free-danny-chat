"use strict";

import {
  requestAction,
  setAppStatus,
  state,
} from "./core.js";

const placesTab = document.getElementById("places-tab");
const placeView = document.getElementById("place-view");
const placeSearchForm = document.getElementById("place-search-form");
const placeSearchInput = document.getElementById("place-search-input");
const placeMap = document.getElementById("place-map");
const placeResults = document.getElementById("place-results");
const placeStatus = document.getElementById("place-status");
const placeLocationButton = document.getElementById("place-location-button");
const placeRoomVerify = document.getElementById("place-room-verify");

Object.assign(state, {
  placeConfig: null,
  placeResults: [],
  placeSearchBusy: false,
  placeMap: null,
  placeMarkers: [],
  placeCurrentLocation: null,
});

let initializationPromise = null;

function setPlaceStatus(copy, isError = false) {
  placeStatus.textContent = copy || "";
  placeStatus.classList.toggle("error", Boolean(isError));
}

function resetPlaces() {
  for (const marker of state.placeMarkers) marker.setMap(null);
  state.placeResults = [];
  state.placeSearchBusy = false;
  state.placeMarkers = [];
  state.placeCurrentLocation = null;
  state.placeMap = null;
  initializationPromise = null;
  placeSearchInput.value = "";
  placeResults.replaceChildren();
  placeMap.replaceChildren();
  placeMap.classList.remove("map-ready");
  setPlaceStatus("");
}

function geolocationPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("이 기기에서는 위치 기능을 사용할 수 없습니다."));
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: 12000,
      maximumAge: 15000,
    });
  });
}

function locationErrorCopy(error) {
  if (error?.code === 1) return "위치 권한이 필요합니다. 브라우저 설정에서 위치 접근을 허용해 주세요.";
  if (error?.code === 2) return "현재 위치를 확인하지 못했습니다. GPS를 켜고 다시 시도해 주세요.";
  if (error?.code === 3) return "위치 확인 시간이 초과되었습니다. 실외에서 다시 시도해 주세요.";
  return error?.message || "위치를 확인하지 못했습니다.";
}

function loadNaverMapScript(clientId) {
  if (window.naver?.maps) return Promise.resolve();
  const existing = document.getElementById("colorless-naver-map-sdk");
  if (existing) {
    return new Promise((resolve, reject) => {
      existing.addEventListener("load", resolve, { once: true });
      existing.addEventListener("error", () => reject(new Error("네이버 지도를 불러오지 못했습니다.")), { once: true });
    });
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.id = "colorless-naver-map-sdk";
    script.async = true;
    script.src = `https://oapi.map.naver.com/openapi/v3/maps.js?ncpKeyId=${encodeURIComponent(clientId)}`;
    script.addEventListener("load", resolve, { once: true });
    script.addEventListener("error", () => reject(new Error("네이버 지도를 불러오지 못했습니다.")), { once: true });
    document.head.appendChild(script);
  });
}

async function initializeMap() {
  const config = state.placeConfig;
  if (!config?.map_enabled || !config.map_client_id) return;
  try {
    await loadNaverMapScript(config.map_client_id);
    if (!window.naver?.maps || state.placeMap) return;
    state.placeMap = new window.naver.maps.Map(placeMap, {
      center: new window.naver.maps.LatLng(37.5111158, 127.098167),
      zoom: 15,
      zoomControl: false,
      mapDataControl: false,
      scaleControl: false,
    });
    placeMap.classList.add("map-ready");
    renderPlaceMarkers();
  } catch (error) {
    setPlaceStatus(error.message, true);
  }
}

function clearPlaceMarkers() {
  for (const marker of state.placeMarkers) marker.setMap(null);
  state.placeMarkers = [];
}

function focusPlace(place) {
  if (!state.placeMap || !window.naver?.maps) return;
  state.placeMap.panTo(new window.naver.maps.LatLng(place.latitude, place.longitude));
  state.placeMap.setZoom(16);
}

function renderPlaceMarkers() {
  if (!state.placeMap || !window.naver?.maps) return;
  clearPlaceMarkers();
  state.placeMarkers = state.placeResults.map((place) => {
    const marker = new window.naver.maps.Marker({
      position: new window.naver.maps.LatLng(place.latitude, place.longitude),
      map: state.placeMap,
      title: place.name,
    });
    window.naver.maps.Event.addListener(marker, "click", () => {
      focusPlace(place);
      document.querySelector(`[data-place-key="${CSS.escape(place.key)}"]`)?.scrollIntoView({ block: "nearest" });
    });
    return marker;
  });
  if (state.placeResults[0]) focusPlace(state.placeResults[0]);
}

async function openPlaceRoom(place, button) {
  button.disabled = true;
  setPlaceStatus(`${place.name} 채팅방을 여는 중입니다.`);
  try {
    const payload = await requestAction("places.open-room", "/place-rooms", {
      method: "POST",
      body: JSON.stringify({ placeToken: place.place_token }),
    });
    const { upsertMessengerRoom } = await import("./messenger.js");
    const { openChatRoom } = await import("./chat.js");
    upsertMessengerRoom(payload.room);
    await openChatRoom(payload.room.id);
    setPlaceStatus("");
  } catch (error) {
    setPlaceStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderPlaces() {
  placeResults.replaceChildren();
  if (state.placeSearchBusy) {
    const loading = document.createElement("p");
    loading.className = "place-empty";
    loading.textContent = "장소를 검색하고 있어요.";
    placeResults.appendChild(loading);
    return;
  }
  if (!state.placeResults.length) {
    const empty = document.createElement("p");
    empty.className = "place-empty";
    empty.textContent = "검색된 장소가 없습니다.";
    placeResults.appendChild(empty);
    clearPlaceMarkers();
    return;
  }
  for (const place of state.placeResults) {
    const item = document.createElement("article");
    item.className = "place-result";
    item.dataset.placeKey = place.key;

    const main = document.createElement("button");
    main.type = "button";
    main.className = "place-result-main";
    main.addEventListener("click", () => focusPlace(place));
    const copy = document.createElement("span");
    copy.className = "place-result-copy";
    const title = document.createElement("strong");
    title.textContent = place.name;
    const address = document.createElement("span");
    address.textContent = place.road_address || place.address || place.category;
    copy.append(title, address);
    const meta = document.createElement("span");
    meta.className = "place-result-meta";
    const participants = document.createElement("span");
    participants.textContent = `대화 ${place.participant_count || 0}명`;
    const verified = document.createElement("span");
    verified.textContent = `현장 인증 ${place.verified_count || 0}명`;
    meta.append(participants, verified);
    main.append(copy, meta);

    const actions = document.createElement("div");
    actions.className = "place-result-actions";
    const external = document.createElement("a");
    external.href = place.external_url;
    external.target = "_blank";
    external.rel = "noopener";
    external.textContent = "지도 앱";
    const enter = document.createElement("button");
    enter.type = "button";
    enter.textContent = "채팅방 들어가기";
    enter.addEventListener("click", () => void openPlaceRoom(place, enter));
    actions.append(external, enter);
    item.append(main, actions);
    placeResults.appendChild(item);
  }
  renderPlaceMarkers();
}

async function searchPlaces(query = "") {
  if (state.placeSearchBusy) return;
  state.placeSearchBusy = true;
  setPlaceStatus("");
  renderPlaces();
  try {
    const payload = await requestAction(
      "places.search",
      `/places/search?q=${encodeURIComponent(query.trim())}`,
      {},
      { key: `places.search:${query.trim() || "popular"}`, policy: "replace" },
    );
    state.placeResults = payload.items || [];
    if (payload.provider === "demo") {
      setPlaceStatus("개발용 장소 데이터입니다. 네이버 검색 키를 연결하면 실제 검색으로 전환됩니다.");
    }
  } catch (error) {
    state.placeResults = [];
    setPlaceStatus(error.message, true);
  } finally {
    state.placeSearchBusy = false;
    renderPlaces();
  }
}

async function initializePlaces() {
  if (initializationPromise) return initializationPromise;
  initializationPromise = (async () => {
    state.placeConfig = await requestAction("places.config", "/places/config", {}, {
      key: "places.config",
      policy: "join",
    });
    await Promise.all([initializeMap(), searchPlaces("")]);
  })().catch((error) => {
    initializationPromise = null;
    setPlaceStatus(error.message, true);
  });
  return initializationPromise;
}

async function moveToCurrentLocation() {
  placeLocationButton.disabled = true;
  setPlaceStatus("현재 위치를 확인하고 있어요.");
  try {
    const position = await geolocationPosition();
    state.placeCurrentLocation = position;
    if (state.placeMap && window.naver?.maps) {
      state.placeMap.panTo(new window.naver.maps.LatLng(
        position.coords.latitude,
        position.coords.longitude,
      ));
      state.placeMap.setZoom(16);
    }
    setPlaceStatus(`현재 위치를 확인했습니다. 정확도 약 ${Math.round(position.coords.accuracy)}m`);
  } catch (error) {
    setPlaceStatus(locationErrorCopy(error), true);
  } finally {
    placeLocationButton.disabled = false;
  }
}

function verificationButtonCopy(verification) {
  const remainingMinutes = Math.max(0, Math.ceil((Number(verification?.expires_at || 0) * 1000 - Date.now()) / 60000));
  if (!remainingMinutes) return "현장 인증";
  const hours = Math.floor(remainingMinutes / 60);
  const minutes = remainingMinutes % 60;
  return `현장 인증 · ${hours ? `${hours}시간 ` : ""}${minutes}분`;
}

function syncPlaceVerificationButton(room) {
  const isPlaceRoom = room?.kind === "place";
  placeRoomVerify.classList.toggle("hidden", !isPlaceRoom);
  if (!isPlaceRoom) return;
  const verification = room.viewer_verification;
  const active = Number(verification?.expires_at || 0) * 1000 > Date.now();
  placeRoomVerify.classList.toggle("verified", active);
  placeRoomVerify.textContent = active ? verificationButtonCopy(verification) : "현장 인증";
  if (room._verificationLoaded || room._verificationLoading) return;
  room._verificationLoading = true;
  void requestAction(
    "places.verification-status",
    `/places/verification?room_id=${encodeURIComponent(room.id)}`,
    {},
    { key: `places.verification-status:${room.id}`, policy: "replace" },
  ).then((payload) => {
    room.viewer_verification = payload.verification || null;
    room._verificationLoaded = true;
    syncPlaceVerificationButton(room);
  }).catch(() => {}).finally(() => { room._verificationLoading = false; });
}

async function verifyCurrentPlaceRoom() {
  const room = state.roomById.get(state.selectedRoomId);
  if (!room || room.kind !== "place") return;
  placeRoomVerify.disabled = true;
  setAppStatus("현재 위치를 확인하고 있어요.");
  try {
    const position = await geolocationPosition();
    const payload = await requestAction("places.verify", "/places/verify", {
      method: "POST",
      body: JSON.stringify({
        roomId: room.id,
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy: position.coords.accuracy,
        capturedAt: position.timestamp,
      }),
    });
    room.viewer_verification = payload.verification;
    room._verificationLoaded = true;
    syncPlaceVerificationButton(room);
    setAppStatus("현장 인증이 완료되었습니다. 배지는 3시간 동안 유지됩니다.", "success");
  } catch (error) {
    setAppStatus(locationErrorCopy(error), "error");
  } finally {
    placeRoomVerify.disabled = false;
  }
}

placeSearchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void searchPlaces(placeSearchInput.value);
});
placeLocationButton.addEventListener("click", () => void moveToCurrentLocation());
placeRoomVerify.addEventListener("click", () => void verifyCurrentPlaceRoom());

export {
  initializePlaces,
  placesTab,
  placeView,
  renderPlaces,
  resetPlaces,
  searchPlaces,
  syncPlaceVerificationButton,
  verifyCurrentPlaceRoom,
};
