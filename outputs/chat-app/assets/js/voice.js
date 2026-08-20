"use strict";

// In-app microphone recording. Audio files are never exposed through the file picker.
const chatVoicePreview = document.getElementById("chat-voice-preview");
const VOICE_RECORDING_MAX_MS = 5 * 60 * 1000;
const VOICE_RECORDING_MIN_MS = 300;
const VOICE_RECORDING_MAX_BYTES = 8 * 1024 * 1024;
const VOICE_RECORDING_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/mp4;codecs=mp4a.40.2",
  "audio/ogg;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg",
];
let activeVoiceRecording = null;
let voicePermissionRequestId = 0;

function formatVoiceDuration(durationMs) {
  const totalSeconds = Math.max(0, Math.round(Number(durationMs || 0) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function supportedVoiceRecordingMimeType() {
  if (typeof MediaRecorder !== "function") return "";
  if (typeof MediaRecorder.isTypeSupported !== "function") return "";
  return VOICE_RECORDING_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function voiceFileDescriptor(recordedType) {
  let contentType = String(recordedType || "").split(";", 1)[0].toLowerCase();
  if (contentType === "video/webm") contentType = "audio/webm";
  const extension = {
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/ogg": "ogg",
  }[contentType];
  return extension ? { contentType, extension } : null;
}

function stopVoiceTracks(stream) {
  for (const track of stream?.getTracks?.() || []) track.stop();
}

function clearVoiceRecordingTimers(recording) {
  window.clearInterval(recording?.statusTimer);
  window.clearTimeout(recording?.limitTimer);
}

function renderVoiceRecorderButton() {
  const recording = state.voiceRecording;
  chatAttachmentButton.classList.toggle("recording", recording);
  chatAttachmentButton.setAttribute("aria-pressed", String(recording));
  chatAttachmentButton.disabled = state.voiceRecordingStarting;
  ColorlessPlatform.decorateIconButton(chatAttachmentButton, recording ? "square" : "paperclip", {
    label: recording ? "음성 녹음 중지" : "첨부 메뉴 열기",
    iconOnly: true,
  });
}

function renderVoiceRecordingStatus(recording) {
  if (activeVoiceRecording !== recording || !state.voiceRecording) return;
  const durationMs = Date.now() - recording.startedAt;
  chatAttachmentPreview.classList.remove("hidden");
  chatAttachmentName.textContent = `● 녹음 중 ${formatVoiceDuration(durationMs)} · 마이크 버튼을 눌러 종료`;
  chatVoicePreview.classList.add("hidden");
}

function releaseVoiceRecording(recording) {
  clearVoiceRecordingTimers(recording);
  stopVoiceTracks(recording?.stream);
  if (activeVoiceRecording === recording) activeVoiceRecording = null;
  state.voiceRecording = false;
  state.voiceRecordingStarting = false;
  renderVoiceRecorderButton();
}

function cancelVoiceRecording() {
  voicePermissionRequestId += 1;
  state.voiceRecordingStarting = false;
  const recording = activeVoiceRecording;
  if (!recording) {
    state.voiceRecording = false;
    renderVoiceRecorderButton();
    return;
  }
  recording.cancelled = true;
  clearVoiceRecordingTimers(recording);
  stopVoiceTracks(recording.stream);
  if (recording.recorder.state !== "inactive") {
    try { recording.recorder.stop(); } catch {}
  } else {
    releaseVoiceRecording(recording);
  }
}

function finishVoiceRecording(recording) {
  const durationMs = Math.min(VOICE_RECORDING_MAX_MS, Date.now() - recording.startedAt);
  const recordedType = recording.recorder.mimeType || recording.mimeType;
  const chunks = recording.chunks.slice();
  const cancelled = recording.cancelled || recording.failed || state.selectedRoomId !== recording.roomId;
  releaseVoiceRecording(recording);
  if (cancelled) {
    renderChatAttachmentPreview();
    return;
  }

  const descriptor = voiceFileDescriptor(recordedType || chunks[0]?.type);
  const blob = new Blob(chunks, { type: descriptor?.contentType || recordedType });
  if (!descriptor || durationMs < VOICE_RECORDING_MIN_MS || !blob.size) {
    renderChatAttachmentPreview();
    setAppStatus("음성이 너무 짧거나 이 브라우저에서 녹음 형식을 지원하지 않아요.", "error");
    return;
  }
  if (blob.size > VOICE_RECORDING_MAX_BYTES) {
    renderChatAttachmentPreview();
    setAppStatus("음성 메시지가 8MB를 넘어 전송할 수 없어요.", "error");
    return;
  }

  const file = new File([blob], `voice_${Date.now()}.${descriptor.extension}`, {
    type: descriptor.contentType,
    lastModified: Date.now(),
  });
  const selectionId = state.chatAttachmentSelectionId + 1;
  state.chatAttachmentSelectionId = selectionId;
  state.chatAttachment = file;
  state.chatAttachmentType = descriptor.contentType;
  state.chatAttachmentKind = "voice";
  state.chatAttachmentDurationMs = durationMs;
  state.chatAttachmentPreviewUrl = URL.createObjectURL(file);
  state.chatAttachmentUpload = startChatAttachmentUpload(
    selectionId,
    file,
    descriptor.contentType,
    { source: "voice-recorder", durationMs },
  );
  renderChatAttachmentPreview();
  setAppStatus(`음성 메시지 ${formatVoiceDuration(durationMs)} 녹음을 마쳤어요. 보내기 버튼을 눌러 주세요.`, "success");
}

function stopVoiceRecording() {
  const recording = activeVoiceRecording;
  if (!recording || recording.recorder.state === "inactive") return;
  try { recording.recorder.requestData?.(); } catch {}
  recording.recorder.stop();
}

async function startVoiceRecording() {
  if (!state.selectedRoomId || state.voiceRecordingStarting || state.voiceRecording) return;
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder !== "function") {
    setAppStatus("이 브라우저에서는 앱 내 음성 녹음을 지원하지 않아요.", "error");
    return;
  }

  clearChatAttachment();
  const roomId = state.selectedRoomId;
  const requestId = ++voicePermissionRequestId;
  state.voiceRecordingStarting = true;
  renderVoiceRecorderButton();
  setAppStatus("마이크 사용 권한을 확인하고 있어요.");
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
  } catch (error) {
    if (requestId !== voicePermissionRequestId) return;
    state.voiceRecordingStarting = false;
    renderVoiceRecorderButton();
    const denied = error?.name === "NotAllowedError" || error?.name === "SecurityError";
    setAppStatus(denied ? "마이크 권한을 허용해야 음성 메시지를 녹음할 수 있어요." : "마이크를 시작하지 못했어요.", "error");
    return;
  }
  if (requestId !== voicePermissionRequestId || state.selectedRoomId !== roomId) {
    stopVoiceTracks(stream);
    return;
  }

  const mimeType = supportedVoiceRecordingMimeType();
  let recorder;
  try {
    recorder = new MediaRecorder(stream, mimeType ? { mimeType, audioBitsPerSecond: 64000 } : undefined);
  } catch {
    stopVoiceTracks(stream);
    state.voiceRecordingStarting = false;
    renderVoiceRecorderButton();
    setAppStatus("이 브라우저의 음성 녹음 형식을 사용할 수 없어요.", "error");
    return;
  }

  const recording = {
    recorder,
    stream,
    roomId,
    mimeType,
    chunks: [],
    startedAt: Date.now(),
    statusTimer: null,
    limitTimer: null,
    cancelled: false,
    failed: false,
  };
  activeVoiceRecording = recording;
  state.voiceRecording = true;
  state.voiceRecordingStarting = false;
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data?.size) recording.chunks.push(event.data);
  });
  recorder.addEventListener("error", () => {
    recording.failed = true;
    setAppStatus("녹음 중 오류가 발생했어요.", "error");
  });
  recorder.addEventListener("stop", () => finishVoiceRecording(recording), { once: true });
  recorder.start(250);
  recording.statusTimer = window.setInterval(() => renderVoiceRecordingStatus(recording), 250);
  recording.limitTimer = window.setTimeout(() => stopVoiceRecording(), VOICE_RECORDING_MAX_MS);
  renderVoiceRecorderButton();
  renderVoiceRecordingStatus(recording);
}

function toggleVoiceRecording() {
  if (state.voiceRecordingStarting) {
    cancelVoiceRecording();
    return;
  }
  if (state.voiceRecording) {
    stopVoiceRecording();
    return;
  }
  void startVoiceRecording();
}
