"use strict";

import { beginAuthRequest, demoLoginButton, googleButtonContainer, googleLoginButton, kakaoLoginButton, loginForm, loginPassword, loginUsername, registerCoreHooks, rememberSession, requestAction, setAuthRequestBusy, setAuthStatus, setProviderStatus, showAuth, state } from "./core.js";
import { startApp } from "./app.js";

// Authentication providers, signup, login, logout, and phone verification.
function setAuthMode(mode) {
  setAuthStatus("SNS 로그인 또는 기존 계정 로그인을 선택해 주세요.");
}

async function loadProviders() {
  try {
    const data = await requestAction("auth.load-providers", "/auth/providers", {}, {
      key: "auth.providers",
      policy: "join",
    });
    state.providers = data.providers || {};
    const googleEnabled = Boolean(state.providers.google?.enabled);
    const kakaoEnabled = Boolean(state.providers.kakao?.enabled);
    const demoEnabled = Boolean(state.providers.demo?.enabled);
    googleLoginButton.disabled = !googleEnabled;
    kakaoLoginButton.disabled = !kakaoEnabled;
    demoLoginButton.disabled = !demoEnabled;

    if (googleEnabled) {
      await renderGoogleButton();
      setProviderStatus("");
    } else if (kakaoEnabled) {
      setProviderStatus("카카오 로그인이 준비됐어요.", "success");
    } else {
      setProviderStatus("구글/카카오 앱 키를 연결하면 SNS 로그인을 사용할 수 있어요.");
    }
  } catch (error) {
    setProviderStatus(error.message, "error");
  }
}

function consumeAuthQuery() {
  const url = new URL(window.location.href);
  const authError = url.searchParams.get("auth_error");
  if (!authError) return;

  const messages = {
    google_not_configured: "구글 클라이언트 ID와 시크릿이 아직 연결되지 않았어요.",
    google_access_denied: "구글 로그인 동의가 취소됐어요.",
    kakao_not_configured: "카카오 앱 키가 아직 연결되지 않았어요.",
    kakao_access_denied: "카카오 로그인 동의가 취소됐어요.",
    oauth_state_invalid: "로그인 보안 검증에 실패했어요. 다시 시도해 주세요.",
    google_login_failed: "구글 로그인 처리 중 문제가 생겼어요.",
    kakao_login_failed: "카카오 로그인 처리 중 문제가 생겼어요.",
  };
  setAuthStatus(messages[authError] || "SNS 로그인 처리 중 문제가 생겼어요.", "error");
  url.searchParams.delete("auth_error");
  window.history.replaceState({}, document.title, `${url.pathname}${url.search ? `?${url.searchParams.toString()}` : ""}`);
}

async function submitLogin(event) {
  event.preventDefault();
  if (!beginAuthRequest("로그인 정보를 확인하고 있어요.")) return;
  try {
    rememberSession(await requestAction("auth.login", "/login", {
      method: "POST",
      body: JSON.stringify({ username: loginUsername.value.trim(), password: loginPassword.value }),
    }));
    loginForm.reset();
    await startApp();
  } catch (error) {
    setAuthStatus(error.message, "error");
  } finally {
    setAuthRequestBusy(false);
  }
}

async function logout() {
  try {
    await requestAction("auth.logout", "/logout", { method: "POST" });
    showAuth();
    setAuthStatus("로그아웃했어요. 쇼츠를 보려면 로그인해 주세요.");
  } catch (error) {
    setAuthStatus(error.message, "error");
  }
}

function loadGoogleIdentityLibrary() {
  if (window.google?.accounts?.id) {
    return Promise.resolve();
  }
  if (window.googleIdentityLibraryPromise) {
    return window.googleIdentityLibraryPromise;
  }

  window.googleIdentityLibraryPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = resolve;
    script.onerror = () => reject(new Error("구글 로그인 도구를 불러오지 못했어요."));
    document.head.appendChild(script);
  });
  return window.googleIdentityLibraryPromise;
}

async function handleGoogleCredential(response) {
  if (!response.credential) {
    setAuthStatus("구글 인증 정보를 받지 못했어요.", "error");
    return;
  }
  if (!beginAuthRequest("구글 계정으로 로그인하고 있어요.")) return;
  try {
    rememberSession(await requestAction("auth.google", "/auth/google/credential", {
      method: "POST",
      body: JSON.stringify({ credential: response.credential }),
    }));
    await startApp();
  } catch (error) {
    setAuthStatus(error.message, "error");
  } finally {
    setAuthRequestBusy(false);
  }
}

async function startGoogleLogin() {
  if (!state.providers.google?.enabled) {
    setAuthStatus("구글 로그인은 앱 키를 연결한 뒤 사용할 수 있어요.", "error");
    return;
  }
  try {
    await renderGoogleButton();
    googleButtonContainer.querySelector("div")?.click();
  } catch (error) {
    setAuthStatus(error.message, "error");
  }
}

async function renderGoogleButton() {
  if (!state.providers.google?.enabled || googleButtonContainer.childElementCount) {
    return;
  }
  await loadGoogleIdentityLibrary();
  window.google.accounts.id.initialize({
    client_id: state.providers.google.client_id,
    auto_select: false,
    callback: handleGoogleCredential,
  });
  window.google.accounts.id.renderButton(googleButtonContainer, {
    theme: "outline",
    size: "large",
    text: "continue_with",
    shape: "rectangular",
    width: Math.floor(googleLoginButton.getBoundingClientRect().width || 320),
  });
  googleLoginButton.classList.add("hidden");
  googleButtonContainer.classList.remove("hidden");
}

function startKakaoLogin() {
  if (!state.providers.kakao?.enabled) {
    setAuthStatus("카카오 로그인은 앱 키를 연결한 뒤 사용할 수 있어요.", "error");
    return;
  }
  window.location.href = state.providers.kakao.login_url || "/auth/kakao/start";
}

async function startDemoLogin() {
  const adminPassword = window.prompt("관리자 비밀번호를 입력하세요.");
  if (adminPassword === null) return;
  if (!beginAuthRequest("체험 계정으로 로그인하고 있어요.")) return;
  try {
    rememberSession(await requestAction("auth.demo", "/auth/demo-login", {
      method: "POST",
      body: JSON.stringify({ provider: "demo", adminPassword }),
    }));
    await startApp();
  } catch (error) {
    setAuthStatus(error.message, "error");
  } finally {
    setAuthRequestBusy(false);
  }
}

registerCoreHooks({ setAuthMode });

export {
  consumeAuthQuery,
  loadGoogleIdentityLibrary,
  loadProviders,
  logout,
  setAuthMode,
  startDemoLogin,
  startGoogleLogin,
  startKakaoLogin,
  submitLogin,
};
