# FREE DANNY

회원가입 → 로그인 → 채팅 흐름으로 만든 모바일형 메신저 MVP입니다.

## 로컬 실행

```powershell
cd C:\Users\sangm\Documents\Codex\2026-08-05\co-2\outputs\chat-app
python server.py
```

브라우저에서 `http://localhost:8765` 를 열면 됩니다.

## 현재 기능

- 구글 OAuth 준비형 SNS 로그인
- 카카오 OAuth 준비형 SNS 로그인
- 개발용 SNS 체험 로그인
- 일반 아이디/비밀번호 로그인
- 휴대폰 인증 기반 회원가입
- 채팅방 생성 / 입장
- SSE 기반 실시간 메시지 반영
- `chat_state.json` 파일 기반 상태 저장

## 공개 배포

이 앱이 **내 컴퓨터에서만이 아니라 누구나 접속 가능하게** 되려면 공개 서버에 배포해야 합니다.

이 저장소에는 이미 Render용 설정 파일 `C:\Users\sangm\Documents\Codex\2026-08-05\co-2\render.yaml` 이 들어 있습니다.

배포 후에는 예를 들어 아래처럼 공개 주소가 생깁니다.

- `https://your-app.onrender.com`

그러면 앱은 그 주소를 기준으로 Google/Kakao OAuth 콜백 URL도 맞춰서 동작합니다.

## Render 배포 순서

1. Render에서 새 Web Service를 만듭니다.
2. 현재 저장소를 연결합니다.
3. 루트의 `render.yaml` 을 사용해 Blueprint 배포를 진행합니다.
4. 배포가 끝나면 공개 주소를 확인합니다.
5. Google / Kakao 개발자 콘솔에 그 공개 주소를 OAuth 콜백 주소로 등록합니다.

## 환경 변수

필수/선택 환경 변수는 아래와 같습니다.

- `HOST`
- `PORT`
- `DATA_DIR`
- `PUBLIC_BASE_URL` (선택)
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI` (선택)
- `KAKAO_REST_API_KEY`
- `KAKAO_CLIENT_SECRET`
- `KAKAO_REDIRECT_URI` (선택)

### `PUBLIC_BASE_URL`

설정하지 않아도 앱은 현재 요청의 도메인을 기준으로 공개 주소를 자동 계산합니다.

예:

- 로컬: `http://localhost:8765`
- 배포: `https://your-app.onrender.com`

커스텀 도메인을 강제로 고정하고 싶을 때만 `PUBLIC_BASE_URL` 을 넣으면 됩니다.

## Google 로그인 설정

구글 로그인을 실제로 쓰려면 아래 값을 Google Cloud Console에 등록해야 합니다.

### 로컬 개발

- 승인된 JavaScript 원본: `http://localhost:8765`
- 승인된 리디렉션 URI: `http://localhost:8765/auth/google/callback`

### 공개 배포

- 승인된 JavaScript 원본: `https://your-app.onrender.com`
- 승인된 리디렉션 URI: `https://your-app.onrender.com/auth/google/callback`

## 카카오 로그인 설정

카카오 로그인도 같은 방식입니다.

### 로컬 개발

- 리디렉션 URI: `http://localhost:8765/auth/kakao/callback`

### 공개 배포

- 리디렉션 URI: `https://your-app.onrender.com/auth/kakao/callback`

## 개발용 SNS 로그인

- 외부 OAuth 키가 없어도 `개발용 SNS 체험 로그인` 버튼으로 바로 테스트할 수 있습니다.
- 실제 외부 호출 없이 로컬 계정을 자동 생성해 로그인합니다.

## 휴대폰 인증 안내

- 현재 휴대폰 인증은 개발용 인증 코드 방식입니다.
- `인증번호 받기`를 누르면 화면에 인증 코드가 표시됩니다.
- 실제 문자 발송은 별도 SMS 서비스 연동이 필요합니다.

## 다음 추천 단계

- Google OAuth 클라이언트 실제 발급
- Kakao OAuth 키 연결
- Render 실제 배포
- 커스텀 도메인 연결
- 친구 목록 / 1:1 채팅
- DB 연동
