from __future__ import annotations

from http import HTTPStatus


class AuthRoutesMixin:
    def start_google_login(self) -> None:
        if not self.context.GOOGLE_CLIENT_ID or not self.context.GOOGLE_CLIENT_SECRET:
            self.redirect("/?auth_error=google_not_configured")
            return

        state = self.context.OAUTH_STATES.create()
        params = {
            "response_type": "code",
            "client_id": self.context.GOOGLE_CLIENT_ID,
            "redirect_uri": self.provider_redirect_uri("google"),
            "scope": "openid profile email",
            "state": state,
            "access_type": "online",
            "include_granted_scopes": "true",
            "prompt": "select_account",
        }
        self.send_response(self.context.HTTPStatus.FOUND)
        self.send_header("Location", f"https://accounts.google.com/o/oauth2/v2/auth?{self.context.urlencode(params)}")
        self.send_header("Set-Cookie", self.context.make_oauth_state_cookie(state, secure=self.cookie_secure()))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def consume_bound_oauth_state(self, state: str) -> bool:
        if not state:
            return False
        cookie_state = self.read_cookie_value(self.context.OAUTH_STATE_COOKIE_NAME)
        if not cookie_state or not self.context.hmac.compare_digest(state, cookie_state):
            return False
        return self.context.OAUTH_STATES.consume(state)

    def redirect_after_oauth(self, location: str, session_token: str = "") -> None:
        self.send_response(self.context.HTTPStatus.FOUND)
        self.send_header("Location", location)
        if session_token:
            self.send_header(
                "Set-Cookie",
                self.context.make_cookie_header(session_token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure()),
            )
        self.send_header("Set-Cookie", self.context.clear_oauth_state_cookie(secure=self.cookie_secure()))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def finish_google_login(self, query: dict[str, list[str]]) -> None:
        state = query.get("state", [""])[0].strip()
        state_is_valid = self.consume_bound_oauth_state(state)
        if "error" in query:
            self.redirect_after_oauth("/?auth_error=google_access_denied" if state_is_valid else "/?auth_error=oauth_state_invalid")
            return

        code = query.get("code", [""])[0].strip()
        if not code or not state_is_valid:
            self.redirect_after_oauth("/?auth_error=oauth_state_invalid")
            return

        try:
            token_payload = self.request_google_token(code)
            profile_payload = self.request_google_user_profile(token_payload["access_token"])
            google_sub = str(profile_payload.get("sub", "")).strip()
            if not google_sub:
                raise ValueError("구글 사용자 정보를 읽지 못했습니다.")

            nickname = (
                str(profile_payload.get("name", "")).strip()
                or str(profile_payload.get("email", "")).split("@")[0].strip()
                or f"google_{google_sub[-6:]}"
            )
            user = self.context.STORE.create_or_update_social_user(
                "google",
                google_sub,
                nickname=nickname,
                status_message="구글로 접속 중",
            )
        except Exception:
            self.redirect_after_oauth("/?auth_error=google_login_failed")
            return

        token = self.context.SESSIONS.create(user["username"])
        self.redirect_after_oauth("/", token)

    def google_id_token_login(self) -> None:
        if not self.allow_request("google-login", 10, 15 * 60):
            return
        is_redirect_login = "application/x-www-form-urlencoded" in self.headers.get("Content-Type", "")
        payload = self.read_form_body() if is_redirect_login else self.read_json_body()
        if payload is None:
            return
        if not self.context.GOOGLE_CLIENT_ID:
            self.google_login_error("구글 로그인 설정이 아직 완료되지 않았어요.", self.context.HTTPStatus.BAD_REQUEST, is_redirect_login)
            return

        if is_redirect_login:
            csrf_cookie = self.read_cookie_value("g_csrf_token")
            csrf_body = str(payload.get("g_csrf_token", "")).strip()
            if not csrf_cookie or not csrf_body or not self.context.hmac.compare_digest(csrf_cookie, csrf_body):
                self.google_login_error("구글 로그인 보안 확인에 실패했어요.", self.context.HTTPStatus.FORBIDDEN, is_redirect_login)
                return

        credential = str(payload.get("credential", "")).strip()
        if not credential:
            self.google_login_error("구글 인증 정보를 받지 못했어요.", self.context.HTTPStatus.BAD_REQUEST, is_redirect_login)
            return

        try:
            profile_payload = self.verify_google_id_token(credential)
            google_sub = str(profile_payload.get("sub", "")).strip()
            if not google_sub:
                raise ValueError("구글 사용자 정보를 읽지 못했습니다.")

            nickname = (
                str(profile_payload.get("name", "")).strip()
                or str(profile_payload.get("email", "")).split("@")[0].strip()
                or f"google_{google_sub[-6:]}"
            )
            user = self.context.STORE.create_or_update_social_user(
                "google",
                google_sub,
                nickname=nickname,
                status_message="구글로 접속 중",
            )
        except Exception:
            self.google_login_error("구글 로그인 처리 중 문제가 생겼어요. 다시 시도해 주세요.", self.context.HTTPStatus.UNAUTHORIZED, is_redirect_login)
            return

        token = self.context.SESSIONS.create(user["username"])
        if is_redirect_login:
            self.send_response(self.context.HTTPStatus.FOUND)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", self.context.make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure()))
            self.end_headers()
            return

        self.send_json(
            {"authenticated": True, "user": user},
            self.context.HTTPStatus.OK,
            headers={"Set-Cookie": self.context.make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure())},
        )

    def google_login_error(self, message: str, status: HTTPStatus, is_redirect_login: bool) -> None:
        if is_redirect_login:
            self.redirect("/?auth_error=google_login_failed")
            return
        self.send_json({"error": message}, status)

    def start_kakao_login(self) -> None:
        if not self.context.KAKAO_REST_API_KEY:
            self.redirect("/?auth_error=kakao_not_configured")
            return

        state = self.context.OAUTH_STATES.create()
        params = {
            "response_type": "code",
            "client_id": self.context.KAKAO_REST_API_KEY,
            "redirect_uri": self.provider_redirect_uri("kakao"),
            "state": state,
        }
        self.send_response(self.context.HTTPStatus.FOUND)
        self.send_header("Location", f"https://kauth.kakao.com/oauth/authorize?{self.context.urlencode(params)}")
        self.send_header("Set-Cookie", self.context.make_oauth_state_cookie(state, secure=self.cookie_secure()))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def finish_kakao_login(self, query: dict[str, list[str]]) -> None:
        state = query.get("state", [""])[0].strip()
        state_is_valid = self.consume_bound_oauth_state(state)
        if "error" in query:
            self.redirect_after_oauth("/?auth_error=kakao_access_denied" if state_is_valid else "/?auth_error=oauth_state_invalid")
            return

        code = query.get("code", [""])[0].strip()
        if not code or not state_is_valid:
            self.redirect_after_oauth("/?auth_error=oauth_state_invalid")
            return

        try:
            token_payload = self.request_kakao_token(code)
            profile_payload = self.request_kakao_user_profile(token_payload["access_token"])
            kakao_id = str(profile_payload.get("id", "")).strip()
            if not kakao_id:
                raise ValueError("카카오 사용자 정보를 읽지 못했습니다.")

            kakao_account = profile_payload.get("kakao_account", {}) or {}
            profile = kakao_account.get("profile", {}) or {}
            nickname = str(profile.get("nickname", "")).strip() or f"kakao_{kakao_id[-6:]}"
            user = self.context.STORE.create_or_update_social_user(
                "kakao",
                kakao_id,
                nickname=nickname,
                status_message="카카오로 접속 중",
            )
        except Exception:
            self.redirect_after_oauth("/?auth_error=kakao_login_failed")
            return

        token = self.context.SESSIONS.create(user["username"])
        self.redirect_after_oauth("/", token)

    def request_google_token(self, code: str) -> dict:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.context.GOOGLE_CLIENT_ID,
            "client_secret": self.context.GOOGLE_CLIENT_SECRET,
            "redirect_uri": self.provider_redirect_uri("google"),
            "code": code,
        }
        return self.context.fetch_json(
            "https://oauth2.googleapis.com/token",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=self.context.urlencode(payload).encode("utf-8"),
        )

    def request_google_user_profile(self, access_token: str) -> dict:
        return self.context.fetch_json(
            "https://openidconnect.googleapis.com/v1/userinfo",
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    def verify_google_id_token(self, credential: str) -> dict:
        payload = self.context.fetch_json(f"https://oauth2.googleapis.com/tokeninfo?{self.context.urlencode({'id_token': credential})}")
        if payload.get("aud") != self.context.GOOGLE_CLIENT_ID:
            raise ValueError("구글 클라이언트 ID가 일치하지 않습니다.")
        if payload.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("구글 토큰 발급자를 확인하지 못했습니다.")
        return payload

    def request_kakao_token(self, code: str) -> dict:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.context.KAKAO_REST_API_KEY,
            "redirect_uri": self.provider_redirect_uri("kakao"),
            "code": code,
        }
        if self.context.KAKAO_CLIENT_SECRET:
            payload["client_secret"] = self.context.KAKAO_CLIENT_SECRET

        return self.context.fetch_json(
            "https://kauth.kakao.com/oauth/token",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
            data=self.context.urlencode(payload).encode("utf-8"),
        )

    def request_kakao_user_profile(self, access_token: str) -> dict:
        return self.context.fetch_json(
            "https://kapi.kakao.com/v2/user/me",
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    def demo_social_login(self) -> None:
        if not self.allow_request("demo-login", 10, 15 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return
        if not self.context.SOCIAL_DEMO_LOGIN_ENABLED or not self.context.SOCIAL_DEMO_ADMIN_PASSWORD:
            self.send_json({"error": "개발용 SNS 로그인이 비활성화되어 있습니다."}, self.context.HTTPStatus.BAD_REQUEST)
            return

        password = str(payload.get("adminPassword", ""))
        if not self.context.hmac.compare_digest(password, self.context.SOCIAL_DEMO_ADMIN_PASSWORD):
            self.send_json({"error": "Administrator password is incorrect."}, self.context.HTTPStatus.FORBIDDEN)
            return

        suffix = self.context.secrets.token_hex(3)
        user = self.context.STORE.create_or_update_social_user(
            "demo",
            f"demo-{suffix}",
            nickname=f"demo_{suffix}",
            status_message="개발용 SNS로 접속 중",
        )
        self.context.STORE.seed_demo_network(user["username"])
        token = self.context.SESSIONS.create(user["username"])
        self.send_json(
            {"authenticated": True, "user": user},
            self.context.HTTPStatus.OK,
            headers={"Set-Cookie": self.context.make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure())},
        )

    def signup(self) -> None:
        if not self.allow_request("signup", 10, 15 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return

        username = str(payload.get("username", "")).strip()
        friend_code = str(payload.get("friendCode", "")).strip()
        password = str(payload.get("password", "")).strip()
        status_message = str(payload.get("statusMessage", "")).strip()
        phone = str(payload.get("phone", "")).strip()
        age_group = str(payload.get("ageGroup", "")).strip()
        gender = str(payload.get("gender", "")).strip()
        verification_token = str(payload.get("verificationToken", "")).strip()

        if not self.context.PHONE_VERIFICATIONS.consume(phone, verification_token):
            self.send_json({"error": "휴대폰 인증을 먼저 완료해 주세요."}, self.context.HTTPStatus.BAD_REQUEST)
            return

        user, error = self.context.STORE.create_local_user(username, friend_code, password, status_message, phone, age_group, gender)
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return

        token = self.context.SESSIONS.create(user["username"])
        self.send_json(
            {"authenticated": True, "user": user},
            self.context.HTTPStatus.CREATED,
            headers={"Set-Cookie": self.context.make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure())},
        )

    def login(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return

        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", "")).strip()
        if not self.allow_request(f"login:{username.lower()[:24]}", 10, 15 * 60):
            return
        user = self.context.STORE.authenticate_user(username, password)
        if user is None:
            self.send_json({"error": "사용자 이름 또는 비밀번호가 올바르지 않습니다."}, self.context.HTTPStatus.UNAUTHORIZED)
            return

        token = self.context.SESSIONS.create(user["username"])
        self.send_json(
            {"authenticated": True, "user": user},
            self.context.HTTPStatus.OK,
            headers={"Set-Cookie": self.context.make_cookie_header(token, max_age=60 * 60 * 24 * 7, secure=self.cookie_secure())},
        )

    def logout(self) -> None:
        token = self.read_session_token()
        self.context.SESSIONS.destroy(token)
        self.send_json(
            {"authenticated": False},
            self.context.HTTPStatus.OK,
            headers={"Set-Cookie": self.context.make_cookie_header("", max_age=0, secure=self.cookie_secure())},
        )

    def request_phone_code(self) -> None:
        if not self.allow_request("phone-code", 5, 10 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return

        phone = str(payload.get("phone", "")).strip()
        try:
            verification = self.context.PHONE_VERIFICATIONS.request_code(phone)
        except ValueError as error:
            self.send_json({"error": str(error)}, self.context.HTTPStatus.BAD_REQUEST)
            return

        response = {
            "ok": True,
            "phoneMasked": verification["phone_masked"],
            "expiresIn": verification["expires_in"],
            "delivery": "dev-preview" if self.context.PHONE_VERIFICATION_MODE != "prod" else "sms",
        }
        if self.context.PHONE_VERIFICATION_MODE != "prod":
            response["devCode"] = verification["code"]
        self.send_json(response, self.context.HTTPStatus.OK)

    def verify_phone_code(self) -> None:
        if not self.allow_request("phone-verify", 10, 10 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return

        phone = str(payload.get("phone", "")).strip()
        code = str(payload.get("code", "")).strip()
        token, error = self.context.PHONE_VERIFICATIONS.verify_code(phone, code)
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return

        self.send_json({"ok": True, "phoneMasked": self.context.mask_phone(phone), "verificationToken": token}, self.context.HTTPStatus.OK)
