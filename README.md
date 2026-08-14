# Colorless

친구와 1:1로 대화하고 YouTube Shorts를 공유할 수 있는 모바일형 메신저 MVP입니다. 별도 프레임워크 없이 Python 표준 라이브러리 서버와 단일 HTML 클라이언트로 동작합니다.

## 주요 기능

- 아이디와 비밀번호 기반 회원가입 및 로그인
- Google, Kakao OAuth 로그인
- 친구 ID 검색과 친구 추가
- 1:1 채팅, 읽음 상태, 접속 상태, 실시간 이벤트
- 이미지 및 PDF 첨부(파일당 최대 8MB)
- 픽셀 아바타와 상태 메시지 편집
- YouTube Data API v3 기반 Shorts 피드와 채팅 공유
- 로컬 JSON 파일 또는 Supabase를 이용한 상태 저장
- Render Blueprint 배포 설정과 `/health` 상태 확인 엔드포인트

## 빠른 시작

### 준비 사항

- Python 3.10 이상
- Git

애플리케이션은 Python 표준 라이브러리만 사용하므로 별도의 패키지 설치가 필요하지 않습니다.

### 실행

```bash
git clone https://github.com/lovesangmin716-hue/free-danny-chat.git
cd free-danny-chat
python outputs/chat-app/server.py
```

브라우저에서 [http://localhost:8765](http://localhost:8765)를 엽니다. 서버 상태는 다음 명령으로 확인할 수 있습니다.

```bash
curl http://localhost:8765/health
```

정상 응답:

```json
{"ok": true, "app_name": "Colorless"}
```

외부 서비스 없이 UI 흐름만 확인하려면 아래의 개발용 로그인 설정을 사용하세요. 회원가입용 휴대폰 인증번호도 개발 모드에서는 화면에 표시됩니다.

## 환경 설정

로컬 설정 파일을 만듭니다.

PowerShell:

```powershell
Copy-Item outputs/chat-app/.env.example outputs/chat-app/.env
```

macOS 또는 Linux:

```bash
cp outputs/chat-app/.env.example outputs/chat-app/.env
```

`outputs/chat-app/.env`는 Git에서 제외됩니다. 실제 키나 Supabase service role key를 커밋하지 마세요.

### 서버와 저장소

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | 서버가 바인딩할 호스트 |
| `PORT` | `8765` | 서버 포트 |
| `PUBLIC_BASE_URL` | 요청 주소에서 계산 | OAuth 콜백에 사용할 공개 기준 URL |
| `DATA_DIR` | `outputs/chat-app` | 로컬 상태와 업로드 파일을 저장할 디렉터리 |
| `STATE_FILE` | `<DATA_DIR>/chat_state.json` | 로컬 JSON 상태 파일 경로 |
| `UPLOADS_DIR` | `<DATA_DIR>/uploads` | 로컬 첨부 파일 디렉터리 |
| `SUPABASE_URL` | 미설정 | Supabase 프로젝트 URL |
| `SUPABASE_SERVICE_ROLE_KEY` | 미설정 | 서버 전용 Supabase service role key |

`SUPABASE_URL`과 `SUPABASE_SERVICE_ROLE_KEY`를 모두 설정하면 JSON 파일 대신 Supabase의 `app_state` 테이블을 사용하고 첨부 파일을 `chat-uploads` 버킷에 저장합니다. 먼저 Supabase SQL Editor에서 [`outputs/chat-app/supabase-schema.sql`](outputs/chat-app/supabase-schema.sql)을 실행하세요.

두 변수가 없으면 상태는 `outputs/chat-app/chat_state.json`, 첨부 파일은 `outputs/chat-app/uploads/`에 저장됩니다. 둘 다 `.gitignore`에 포함되어 있습니다.

### Google 로그인과 YouTube Shorts

| 변수 | 설명 |
| --- | --- |
| `GOOGLE_CLIENT_ID` | Google OAuth 웹 클라이언트 ID |
| `GOOGLE_CLIENT_SECRET` | 서버 측 OAuth 코드 흐름에 사용하는 클라이언트 보안 비밀 |
| `GOOGLE_REDIRECT_URI` | 선택 사항. 기본값은 `<PUBLIC_BASE_URL>/auth/google/callback` |
| `YOUTUBE_API_KEY` | YouTube Data API v3용 서버 API 키 |

Google Cloud Console의 승인된 JavaScript 원본에 로컬 주소를 등록합니다.

```text
http://localhost:8765
http://127.0.0.1:8765
```

OAuth 코드 흐름을 사용할 때의 로컬 리디렉션 URI는 다음과 같습니다.

```text
http://localhost:8765/auth/google/callback
```

YouTube API 키에는 YouTube Data API v3만 허용하고 적절한 할당량과 키 제한을 적용하세요. 키는 브라우저로 전달되지 않습니다.

### Kakao 로그인

| 변수 | 설명 |
| --- | --- |
| `KAKAO_REST_API_KEY` | Kakao 앱의 REST API 키 |
| `KAKAO_CLIENT_SECRET` | 선택 사항. 활성화한 경우에만 설정 |
| `KAKAO_REDIRECT_URI` | 선택 사항. 기본값은 `<PUBLIC_BASE_URL>/auth/kakao/callback` |

Kakao Developers에 다음 로컬 리디렉션 URI를 등록합니다.

```text
http://localhost:8765/auth/kakao/callback
```

### 개발용 인증

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `SOCIAL_DEMO_LOGIN_ENABLED` | `true` | 개발용 SNS 체험 로그인 노출 여부 |
| `SOCIAL_DEMO_ADMIN_PASSWORD` | 미설정 | 체험 로그인에 요구할 관리자 비밀번호 |
| `PHONE_VERIFICATION_MODE` | `dev` | `dev`에서는 인증번호를 응답과 화면에 표시 |

공개 환경에서는 개발용 SNS 로그인을 끄세요.

```text
SOCIAL_DEMO_LOGIN_ENABLED=false
```

현재 저장소에는 SMS 발송 연동이 없습니다. `PHONE_VERIFICATION_MODE=prod`로 바꾸면 인증번호를 화면에서 숨기지만 문자를 보내지는 않으므로, 실제 배포 전에 SMS 공급자 연동이 필요합니다.

## Render 배포

루트의 [`render.yaml`](render.yaml)은 `outputs/chat-app`을 서비스 루트로 사용합니다.

1. 이 저장소를 GitHub에 푸시합니다.
2. Render에서 새 Blueprint를 만들고 저장소를 연결합니다.
3. 필요한 OAuth, YouTube, Supabase 환경 변수를 Render에 등록합니다.
4. 배포된 도메인을 Google과 Kakao의 승인된 원본 및 리디렉션 URI에 추가합니다.

Render는 `python -m py_compile server.py`로 빌드를 확인한 뒤 `python server.py`로 서버를 시작합니다. 헬스 체크 경로는 `/health`입니다.

Render의 임시 파일 시스템만 사용하면 재배포 때 로컬 JSON 상태와 첨부 파일이 사라질 수 있습니다. 데이터를 유지해야 하는 배포에서는 Supabase 설정을 사용하세요.

## 프로젝트 구조

```text
.
├── outputs/chat-app/
│   ├── assets/fonts/       # 로컬 웹폰트
│   ├── .env.example        # 환경 변수 예시
│   ├── index.html          # 화면, 스타일, 브라우저 로직
│   ├── server.py           # HTTP API, 인증, 채팅, 저장소 로직
│   └── supabase-schema.sql # 상태 테이블과 업로드 버킷
├── .gitattributes
├── .gitignore
├── README.md
└── render.yaml
```

## 개발 확인

현재 자동화된 테스트 스위트는 없습니다. 변경 전후에 최소한 Python 문법 검사와 상태 엔드포인트를 확인하세요.

```bash
python -m py_compile outputs/chat-app/server.py
curl http://localhost:8765/health
```

프런트엔드는 빌드 단계가 없는 단일 HTML 파일입니다. 모바일 화면, 로그인, 친구 추가, 채팅 전송, 첨부 업로드를 브라우저에서 직접 확인해야 합니다.

## 알려진 제한 사항

- 휴대폰 인증은 개발용 코드 미리보기만 구현되어 있습니다.
- 자동화된 테스트와 CI가 아직 없습니다.
- 로컬 모드는 단일 JSON 문서에 상태를 저장하므로 다중 인스턴스 운영에 적합하지 않습니다.
- 세션과 접속 상태는 서버 메모리에 있으므로 서버 재시작 시 초기화됩니다.
- `index.html`과 `server.py`에 기능이 집중되어 있어 규모가 커지면 모듈 분리가 필요합니다.

## 기여

작은 단위로 변경하고, 커밋에 비밀 값이나 런타임 데이터를 포함하지 마세요. Pull Request에는 변경 이유와 직접 확인한 흐름을 적어 주세요.

## 라이선스

이 저장소에는 아직 라이선스가 지정되어 있지 않습니다. 재사용이나 배포 권한이 필요하면 저장소 소유자에게 먼저 확인하세요.
