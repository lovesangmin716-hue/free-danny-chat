"use strict";

const signupState = { phone: "", token: "", verified: false, busy: false };
const signupForm = document.getElementById("signup-form");
const signupStatus = document.getElementById("signup-status");
const signupUsername = document.getElementById("signup-username");
const signupFriendCode = document.getElementById("signup-friend-code");
const signupPassword = document.getElementById("signup-password");
const signupPasswordConfirm = document.getElementById("signup-password-confirm");
const signupAgeGroup = document.getElementById("signup-age-group");
const signupGender = document.getElementById("signup-gender");
const signupPhone = document.getElementById("signup-phone");
const signupCode = document.getElementById("signup-code");
const phoneRequestButton = document.getElementById("phone-request-button");
const phoneVerifyButton = document.getElementById("phone-verify-button");
const phoneHelp = document.getElementById("phone-help");
const phoneVerifiedBadge = document.getElementById("phone-verified-badge");
const signupSubmitButton = document.getElementById("signup-submit-button");

function normalizePhone(value) {
  return (value || "").replace(/\D/g, "").slice(0, 11);
}

function setSignupStatus(message, tone = "default") {
  signupStatus.textContent = message;
  signupStatus.className = `signup-status${tone === "default" ? "" : ` ${tone}`}`;
}

function resetVerification() {
  signupState.phone = "";
  signupState.token = "";
  signupState.verified = false;
  phoneVerifiedBadge.classList.add("hidden");
  signupSubmitButton.disabled = true;
}

async function signupApi(url, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const contentType = response.headers.get("Content-Type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
    try { payload = await response.json(); } catch (_) { payload = null; }
  }
  if (!response.ok) {
    const status = `${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;
    throw new Error(payload?.error || `${method} ${url} 요청 실패 (HTTP ${status})`);
  }
  return payload;
}

async function requestPhoneCode() {
  const phone = normalizePhone(signupPhone.value);
  signupPhone.value = phone;
  if (phone.length < 10) {
    setSignupStatus("휴대폰 번호를 정확히 입력해 주세요.", "error");
    signupPhone.focus();
    return;
  }
  phoneRequestButton.disabled = true;
  try {
    const response = await signupApi("/phone/request-code", {
      method: "POST",
      body: JSON.stringify({ phone }),
    });
    resetVerification();
    signupState.phone = phone;
    const devCode = response.devCode ? ` 개발용 인증번호: ${response.devCode}` : "";
    phoneHelp.textContent = `${response.phoneMasked} 번호로 인증번호를 준비했어요.${devCode}`;
    setSignupStatus("인증번호를 입력해 주세요.", "success");
    signupCode.focus();
  } catch (error) {
    setSignupStatus(error.message, "error");
  } finally {
    phoneRequestButton.disabled = false;
  }
}

async function verifyPhoneCode() {
  const phone = normalizePhone(signupPhone.value);
  const code = signupCode.value.replace(/\D/g, "").slice(0, 6);
  signupCode.value = code;
  if (phone.length < 10 || code.length !== 6) {
    setSignupStatus("휴대폰 번호와 인증번호 6자리를 입력해 주세요.", "error");
    return;
  }
  phoneVerifyButton.disabled = true;
  try {
    const response = await signupApi("/phone/verify-code", {
      method: "POST",
      body: JSON.stringify({ phone, code }),
    });
    signupState.phone = phone;
    signupState.token = response.verificationToken;
    signupState.verified = true;
    phoneVerifiedBadge.classList.remove("hidden");
    phoneHelp.textContent = `${response.phoneMasked} 번호 인증이 완료됐어요.`;
    signupSubmitButton.disabled = false;
    setSignupStatus("인증 완료. 계정을 만들 수 있어요.", "success");
  } catch (error) {
    resetVerification();
    setSignupStatus(error.message, "error");
  } finally {
    phoneVerifyButton.disabled = false;
  }
}

async function submitSignup(event) {
  event.preventDefault();
  if (signupState.busy) return;
  const phone = normalizePhone(signupPhone.value);
  if (signupPassword.value !== signupPasswordConfirm.value) {
    setSignupStatus("비밀번호 확인이 일치하지 않습니다.", "error");
    signupPasswordConfirm.focus();
    return;
  }
  if (!signupState.verified || signupState.phone !== phone || !signupState.token) {
    setSignupStatus("휴대폰 인증을 먼저 완료해 주세요.", "error");
    signupPhone.focus();
    return;
  }
  signupState.busy = true;
  signupSubmitButton.disabled = true;
  signupSubmitButton.textContent = "계정 만드는 중...";
  try {
    await signupApi("/signup", {
      method: "POST",
      body: JSON.stringify({
        username: signupUsername.value.trim(),
        friendCode: signupFriendCode.value.trim(),
        password: signupPassword.value,
        statusMessage: "",
        phone,
        verificationToken: signupState.token,
        ageGroup: signupAgeGroup.value,
        gender: signupGender.value,
      }),
    });
    window.location.replace("/");
  } catch (error) {
    setSignupStatus(error.message, "error");
    signupState.busy = false;
    signupSubmitButton.disabled = false;
    signupSubmitButton.textContent = "가입하고 시작하기";
  }
}

signupPhone.addEventListener("input", () => {
  const normalized = normalizePhone(signupPhone.value);
  signupPhone.value = normalized;
  if (normalized !== signupState.phone) resetVerification();
});
signupCode.addEventListener("input", () => {
  signupCode.value = signupCode.value.replace(/\D/g, "").slice(0, 6);
});
phoneRequestButton.addEventListener("click", requestPhoneCode);
phoneVerifyButton.addEventListener("click", verifyPhoneCode);
signupForm.addEventListener("submit", submitSignup);

signupApi("/session", { headers: {} })
  .then((session) => { if (session.authenticated) window.location.replace("/"); })
  .catch(() => setSignupStatus("서버 연결을 확인해 주세요.", "error"));
