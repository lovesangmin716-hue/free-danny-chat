# Colorless

친구와 1:1로 대화하고 YouTube Shorts를 공유할 수 있는 모바일형 메신저 MVP입니다. 별도 프레임워크 없이 Python 표준 라이브러리 서버와 단일 HTML 클라이언트로 동작합니다.

## 주요 기능

- 아이디와 비밀번호 기반 회원가입 및 로그인
- Google, Kakao OAuth 로그인
- 친구 ID 검색과 친구 추가
- 1:1 채팅, 읽음 상태, 접속 상태, 실시간 이벤트
- 이미지, PDF, 텍스트·CSV·Markdown·RTF·ZIP 및 Office 문서 첨부와 채팅 입력창 붙여넣기(이미지 원본은 최대 50MB까지 선택 가능하며 브라우저에서 WebP로 줄인 뒤 8MB 이하만 전송, 나머지 파일은 최대 8MB)
- 픽셀 아바타 편집과 프로필 사진 업로드(픽셀 원본은 별도 3KB RGB 리소스로 저장·편집기를 열 때만 조회하고, 목록은 immutable 버전 썸네일만 지연 로딩)
- YouTube Data API v3 기반 Shorts 피드와 채팅 공유
- 로컬 SQLite 또는 Supabase를 이용한 증분 상태 저장
- Render Blueprint 배포 설정과 분리된 `/live`·`/ready`, 구조화 로그·운영 메트릭

## 빠른 시작

### 준비 사항

- Python 3.10 이상
- Git

`pyproject.toml`이 Python 패키지와 정적 웹 리소스를 함께 설치합니다.

### 실행

```bash
git clone https://github.com/lovesangmin716-hue/free-danny-chat.git
cd free-danny-chat
python -m pip install -e .
python -m colorless
```

브라우저에서 [http://localhost:8765](http://localhost:8765)를 엽니다. 서버 상태는 다음 명령으로 확인할 수 있습니다.

```bash
curl http://localhost:8765/health
```

정상 응답:

```json
{"ok": true, "status": "live", "app_name": "Colorless"}
```

외부 서비스 없이 UI 흐름만 확인하려면 아래의 개발용 로그인 설정을 사용하세요. 회원가입용 휴대폰 인증번호도 개발 모드에서는 화면에 표시됩니다.

## 환경 설정

로컬 설정 파일을 만듭니다.

PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS 또는 Linux:

```bash
cp .env.example .env
```

루트의 `.env`는 Git에서 제외됩니다. 다른 설정 파일을 사용하려면 `COLORLESS_ENV_FILE`에 경로를 지정하세요. 실제 키나 Supabase service role key를 커밋하지 마세요.

### 서버와 저장소

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | 서버가 바인딩할 호스트 |
| `PORT` | `8765` | 서버 포트 |
| `PUBLIC_BASE_URL` | 로컬에서는 요청 주소에서 계산 | OAuth 콜백에 사용할 공개 기준 URL. 운영에서 OAuth를 켜면 필수 |
| `DATA_DIR` | `<실행 디렉터리>/.colorless-data` | 로컬 상태와 업로드 파일을 저장할 디렉터리 |
| `STATE_FILE` | `<DATA_DIR>/chat_state.json` | 기존 JSON을 가져올 경로이자 SQLite 파일 이름의 기준 경로. 실제 DB는 `<STATE_FILE>.sqlite3`에 생성 |
| `UPLOADS_DIR` | `<DATA_DIR>/uploads` | 로컬 첨부 파일 디렉터리 |
| `MAX_REQUEST_THREADS` | `64` | SSE를 포함한 프로세스 전체 동시 요청 스레드 상한 |
| `MAX_BODY_READERS` | `16` | 동시에 요청 본문을 읽을 수 있는 스레드 상한 |
| `HEADER_READ_TIMEOUT_SECONDS` | `5` | 완전한 HTTP 헤더를 기다리는 최대 시간 |
| `BODY_READ_TIMEOUT_SECONDS` | `10` | JSON·폼 본문을 전부 읽는 최대 시간 |
| `UPLOAD_READ_TIMEOUT_SECONDS` | `30` | 로컬 fallback에서 최대 8MB 업로드 스트림을 읽는 최대 시간 |
| `UPLOAD_GRANT_TTL_SECONDS` | `600` | 미완료 업로드 권한과 임시 객체를 유지하는 시간(초) |
| `DOWNLOAD_URL_TTL_SECONDS` | `60` | 권한 확인 뒤 발급하는 object storage 다운로드 URL 수명(초) |
| `MAX_SSE_CONNECTIONS` | `32` | 프로세스당 동시 실시간 이벤트 연결 상한 |
| `MAX_SSE_QUEUE_SIZE` | `32` | 연결별 이벤트 큐 상한. 포화된 느린 연결은 종료 후 재동기화 |
| `SSE_HEARTBEAT_SECONDS` | `10` | 끊긴 실시간 연결을 감지하는 heartbeat 간격 |
| `EVENT_POLL_INTERVAL_SECONDS` | `0.1` | 공유 durable event log를 소비하는 간격 |
| `PRESENCE_TTL_SECONDS` | `45` | 서버 장애 후 공유 presence lease가 자동 만료되는 시간(최대 60초) |
| `INSTANCE_ID` | 자동 생성 | 이벤트 발생 서버와 presence lease를 구분하는 인스턴스 식별자 |
| `SHORTS_COLLECTION_INTERVAL_SECONDS` | `1800` | 공유 YouTube catalog 수집 작업 사이의 최소 간격 |
| `SHORTS_COLLECTION_LEASE_SECONDS` | `120` | 다중 인스턴스 중 한 수집기만 실행하게 하는 lease 수명 |
| `SHORTS_DAILY_QUOTA_BUDGET` | `5000` | 모든 인스턴스가 공유하는 일일 YouTube 수집 quota 상한 |
| `SHORTS_CATALOG_TTL_SECONDS` | `21600` | 최근 수집 catalog를 fresh로 간주하는 시간 |
| `SHORTS_CATALOG_RETENTION_SECONDS` | `604800` | 장애 시 stale 제공 후 오래된 후보를 제거하는 보존 기간 |
| `SUPABASE_URL` | 미설정 | Supabase 프로젝트 URL |
| `SUPABASE_SERVICE_ROLE_KEY` | 미설정 | 서버 전용 Supabase service role key |
| `REQUIRE_SUPABASE` | `false` | `true`이면 Supabase 설정이 없을 때 서버 시작을 중단해 임시 파일 저장을 방지 |
| `LOCAL_SIGNUP_ENABLED` | `true` | 신규 로컬 회원가입과 휴대폰 인증 API 노출 여부. SMS 발송 연동 전 운영에서는 `false` |

`SUPABASE_URL`과 `SUPABASE_SERVICE_ROLE_KEY`를 모두 설정하면 Supabase와 private `chat-uploads` 버킷을 사용합니다. 브라우저는 객체 하나에 한정된 signed upload URL로 저장소에 직접 전송하고, 서버는 크기·MIME·magic bytes를 확인한 뒤에만 메시지 첨부를 허용합니다. 다운로드는 방 접근 권한을 확인한 뒤 기본 60초 signed URL로 redirect하므로 정상 파일 바이트는 앱 서버를 지나지 않습니다. Supabase signed upload token 자체의 유효기간은 플랫폼이 정한 2시간이며, 앱의 pending grant는 기본 10분 뒤 만료되어 첨부에 사용할 수 없습니다. 최신 [`src/colorless/database/supabase-schema.sql`](src/colorless/database/supabase-schema.sql)은 사용자·관계·방·멤버·메시지·읽음 위치·세션·Shorts 상태의 정규화 테이블과 필수 제약/인덱스를 생성하며, 전환 검증과 rollback 동안 기존 `app_state`도 보존합니다.

서버는 문서, JSON, 정적 asset, 인증된 업로드를 포함한 모든 HTTP 응답에 CSP, MIME sniffing 차단, framing 차단, Referrer Policy, Permissions Policy를 공통 적용합니다. CSP는 자체 리소스와 Google Identity/YouTube에 필요한 origin만 허용합니다. HTTPS로 전달된 운영 요청에는 HSTS도 추가되므로 Render 앞단에서 `X-Forwarded-Proto: https`가 유지되어야 합니다.

두 변수가 없으면 상태는 `.colorless-data/chat_state.json.sqlite3`, 첨부 파일은 `.colorless-data/uploads/`에 저장됩니다. 다만 새 데이터 디렉터리가 없고 기존 `outputs/chat-app`에 로컬 DB나 업로드가 있으면 데이터 유실을 피하기 위해 그 위치를 자동으로 이어서 사용합니다. 이 fallback은 요청을 64KB씩 `.part` 파일로 기록하고 magic bytes와 정확한 크기를 검증한 뒤 atomic rename하며, 다운로드의 단일 `Range` 요청과 backpressure를 지원합니다. 런타임 상태와 업로드 파일은 `.gitignore`에 포함되어 있습니다.

로컬 SQLite는 최초 실행에 기존 `state_parts`를 정규화 행으로 원자적으로 가져옵니다. 이후 메시지는 `messages`에 동기 INSERT되고 방별 JSON 배열을 다시 쓰거나 프로세스 메모리에 전체 적재하지 않습니다. 운영 데이터는 오프라인 전환 전에 백업과 건수/FK 검증을 실행하세요.

```bash
colorless-migrate .colorless-data/chat_state.json.sqlite3
colorless-migrate .colorless-data/chat_state.json.sqlite3 --verify-only
```

첫 명령은 기본적으로 `.pre-normalized.bak` 백업을 만든 뒤 한 트랜잭션으로 변환합니다. 검증이 실패하면 서버를 내리고 백업 DB로 원본을 복원할 수 있으며, `state_parts` 자체도 전환 안정화 기간 동안 삭제하지 않습니다.

Supabase 운영 전환은 쓰기를 잠근 유지보수 창에서 진행합니다.

1. Supabase의 Point-in-Time Recovery 또는 수동 백업을 만들고 `app_state`를 별도로 내보냅니다.
2. [`src/colorless/database/supabase-schema.sql`](src/colorless/database/supabase-schema.sql)을 적용합니다. 정규화 테이블은 `service_role`만 직접 접근할 수 있고 여러 행을 바꾸는 명령은 트랜잭션 RPC로 노출됩니다.
3. 새 서버를 시작합니다. `app_migrations.normalized_state`가 없으면 서버가 기존 `app_state`를 멱등 upsert로 가져오고, 모든 단계가 성공한 뒤에만 전환 마커를 기록합니다.
4. SQL Editor에서 `select public.colorless_storage_counts();`와 `select * from public.app_migrations where key = 'normalized_state';`를 확인한 뒤 쓰기를 다시 엽니다. 서버 복원은 고정 정렬과 `limit`/`offset` 페이지를 사용하므로 PostgREST의 1,000행 응답 상한을 넘는 계정과 방도 누락하지 않습니다.

검증 전에 실패하면 마커를 기록하지 않으므로 원인을 수정한 뒤 가져오기를 다시 실행할 수 있습니다. 이전 서버로 rollback할 때 새 서버가 아직 쓰기를 받지 않았다면 보존된 `app_state`를 그대로 사용합니다. 쓰기를 다시 연 뒤 rollback해야 한다면 구형 `app_state`는 새 변경을 포함하지 않으므로 서버를 내리고 전환 직전 Supabase 백업을 복원해야 합니다.

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

운영에서는 `PUBLIC_BASE_URL`을 실제 HTTPS origin으로 명시해야 합니다. OAuth 시작 시 Google과 Kakao 모두 짧은 수명의 HttpOnly/SameSite=Lax state 쿠키를 발급하며, callback은 해당 쿠키 일치와 서버 state의 일회성 소비를 모두 요구합니다. callback 성공·실패 후에는 state 쿠키가 즉시 만료됩니다.

YouTube API 키에는 YouTube Data API v3만 허용하고 적절한 할당량과 키 제한을 적용하세요. 키는 브라우저로 전달되지 않습니다.

YouTube 호출은 사용자 `/youtube/shorts` 요청에서 실행되지 않습니다. 백그라운드 수집기가 공유 `shorts_catalog`에 후보를 적재하고, 분산 lease와 공유 quota 행으로 여러 인스턴스의 중복 수집을 막습니다. 사용자 요청은 catalog 조회와 개인 `shorts_seen` 필터만 수행합니다. 429/403은 circuit을 즉시 열고, 일반 장애는 세 번 연속 실패하면 circuit을 열며, 그동안 최근 성공 catalog를 최대 7일 정책으로 stale 제공하고 이후 제거합니다. `/metrics`의 `shorts_catalog`에서 catalog age, fresh/stale hit, quota, 실패 및 circuit 상태를 확인할 수 있습니다.

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

현재 저장소에는 SMS 발송 연동이 없습니다. `PHONE_VERIFICATION_MODE=prod`로 바꾸면 인증번호를 화면에서 숨기지만 문자를 보내지는 않습니다. 그래서 Render 기본 설정은 `LOCAL_SIGNUP_ENABLED=false`로 신규 로컬 회원가입을 닫고 OAuth와 기존 로컬 계정 로그인만 허용합니다. SMS 공급자를 연동한 뒤에만 이 값을 `true`로 바꾸세요.

## Render 배포

루트의 [`render.yaml`](render.yaml)은 저장소 루트에서 `colorless` 패키지를 설치합니다.

1. Supabase 백업과 `app_state` 내보내기를 만든 뒤 최신 [`src/colorless/database/supabase-schema.sql`](src/colorless/database/supabase-schema.sql)을 먼저 적용합니다. 이 단계가 기존 사용자에서 계정과 첫 번째 아이덴티티를 분리하고 세션을 보강합니다.
2. `python tests/deploy_preflight.py`로 소스·Blueprint·스키마 필수 항목을 확인한 뒤 이 저장소를 GitHub에 푸시합니다.
3. Render에서 새 Blueprint를 만들고 저장소를 연결합니다.
4. Blueprint 생성 화면에서 `PUBLIC_BASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`와 사용할 Google/Kakao 로그인 자격 증명을 등록합니다.
5. 같은 운영 환경 변수로 `python tests/deploy_preflight.py --environment --remote`를 실행하고, 배포된 도메인을 Google과 Kakao의 승인된 원본 및 리디렉션 URI에 추가합니다.
6. 배포 후 `/live`와 `/ready`가 모두 200인지 확인하고 아래 SQL 결과가 각각 0행/0건인지 확인합니다.

```sql
select count(*) from public.users where account_id is null;
select account_id, count(*) from public.users group by account_id having count(*) > 3;
select count(*) from public.sessions where account_id is null or active_user_id is null;
```

Render는 Node.js 22로 프런트엔드를 빌드해 asset fingerprint를 갱신하고 운영 Supabase의 계정 테이블·세션·무결성 RPC를 읽기 전용으로 검사한 다음 Python 3.12 패키지와 웹 산출물을 함께 설치해 `python -m colorless`로 서버를 시작합니다. 스키마가 아직 적용되지 않았거나 데이터 무결성 검사가 실패하면 새 배포를 시작하지 않습니다. 헬스 체크는 DB·migration·핵심 queue까지 확인하는 `/ready`를 사용하며 GitHub 검사가 성공한 커밋만 자동 배포합니다.

Render Blueprint는 `REQUIRE_SUPABASE=true`로 실행됩니다. Supabase 환경 변수가 빠지면 서버가 시작되지 않으므로, 임시 파일 시스템에 계정과 첨부 파일이 저장되는 배포를 방지합니다.

스키마 적용 전에는 새 서버를 배포하지 마세요. 문제가 생기면 Render에서 직전 정상 커밋으로 되돌리고, 새 서버가 쓰기를 받기 전이라면 보존된 `app_state`를 사용합니다. 쓰기 재개 후 데이터 문제가 확인되면 서비스를 내리고 전환 직전 Supabase 백업을 복원해야 합니다.

## 프로젝트 구조

```text
.
├── apps/
│   ├── desktop/            # 향후 데스크톱 클라이언트
│   └── mobile/             # 향후 모바일 클라이언트
├── frontend/
│   ├── src/app/            # 웹 기능·platform ES Module 원본
│   ├── src/*.js            # 브라우저 성능 검증 fixture 원본
│   ├── scripts/build.mjs   # esbuild 번들·해시·HTML 갱신
│   └── package.json        # 프런트엔드 빌드 의존성
├── src/colorless/
│   ├── database/           # Supabase 스키마
│   ├── http/               # 인증·메시징·쇼츠·업로드 HTTP 기능 라우트
│   ├── web/                # 패키지에 포함되는 HTML과 빌드 산출물
│   │   └── assets/js/      # minify된 main·signup·Worker 번들
│   ├── __main__.py         # `python -m colorless` 진입점
│   ├── application.py      # 기능 명령과 domain event 결과
│   ├── cache.py            # 단일 실행 TTL 캐시
│   ├── config.py           # 환경 변수, 제한값, 보안 정책
│   ├── integrations.py     # 외부 HTTP와 Supabase 공통 요청
│   ├── observability.py    # 요청/SSE 메트릭과 구조화 로그
│   ├── persistence.py      # SQLite와 Supabase 저장소
│   ├── realtime.py         # durable event와 다중 인스턴스 replay
│   ├── runtime.py          # 세션, 업로드, presence 런타임 저장소
│   ├── shorts.py           # YouTube Shorts catalog 수집기
│   ├── state.py            # 채팅 상태·인덱스·저장소 조정
│   ├── utils.py            # 식별자, 이미지, 쿠키 검증 유틸리티
│   ├── web_resources.py    # 정적 리소스 압축·fingerprint 로더
│   └── server.py           # 조립, 공통 HTTP dispatch, 프로세스 lifecycle
├── tests/
│   └── test_server.py      # 권한, 읽음 상태, 저장소 경계 테스트
├── .env.example            # 환경 변수 예시
├── pyproject.toml          # 패키지·의존성·CLI 정의
├── .gitattributes
├── .gitignore
├── README.md
├── ARCHITECTURE.md         # 통합 처리 파이프라인과 모듈 경계
├── OPERATIONS.md           # SLO, 로그·메트릭, probe, 부하/장애 runbook
└── render.yaml
```

## 처리 파이프라인

개발 소스는 `frontend/src/app/entrypoints/main.js`에서 시작하며 기능과 platform 파일을 명시적인 ES Module `import`/`export`로 연결합니다. esbuild는 이 그래프를 minify된 `src/colorless/web/assets/js/main.js`로 만들고, Python wheel에는 실행용 산출물만 포함됩니다. 모든 기능 요청은 `requestAction()`을 통해 공통 액션 파이프라인과 HTTP 클라이언트를 통과합니다. 실시간 이벤트는 타입별 이벤트 라우터가 기능 핸들러로 전달하며, 상태 변경은 이름이 붙은 스토어 트랜잭션으로 기록됩니다.

서버의 핵심 변경 명령은 `run_json_command()`에서 `ApplicationServices`를 호출합니다. 서비스는 HTTP 응답을 직접 작성하지 않고 데이터, 상태 코드, 발행할 이벤트가 포함된 `CommandOutcome`을 반환합니다. 세부 원칙과 흐름은 [`ARCHITECTURE.md`](ARCHITECTURE.md)를 참고하세요.

## 개발 확인

서버 테스트는 Python 표준 라이브러리의 `unittest`로 실행하며 개발 전 `python -m pip install -e .`로 패키지를 설치합니다. 브라우저 빌드와 JavaScript 문법 검사는 Node.js 22 이상을 사용합니다.

```bash
cd frontend
npm ci
npm run build
cd ..
python -m unittest discover -s tests -v
python tests/js_syntax.py
python tests/static_budget.py
python tests/multi_instance.py
python tests/bootstrap_scale.py --count 1000 --iterations 20
python tests/operations_load.py --profile smoke
```

테스트는 채팅방별 이벤트 권한, 읽음 상태 중복 알림 방지, 첨부 파일 접근 권한, 세션 만료와 재시작 복원, 요청 제한, 증분 상태 저장, 접속 상태 인덱스, 외부 API 요청 병합을 확인합니다. `static_budget.py`는 TTF/OTF 포함, stale fingerprint, JS/CSS/font/image/HTML 용량 초과를 CI에서 차단합니다. `multi_instance.py`는 임시 공유 DB에 실제 서버 두 개를 띄워 서버 간 메시지 전달, 동일 `client_message_id` 재시도, 한 서버 중단 중 발생한 메시지의 cursor replay를 검사합니다. `bootstrap_scale.py`는 친구와 방을 각각 1,000개 만든 뒤 최초 30개 응답의 p95·gzip 크기와 전체 cursor 순회의 중복·누락을 검사합니다. `operations_load.py`의 smoke profile은 로그인, 메시지, SSE, 업로드, Shorts, DB 장애를 실제 HTTP로 실행하고 SLO 회귀를 CI에서 차단합니다. 운영 SLO와 load/soak/spike 절차는 [`OPERATIONS.md`](OPERATIONS.md)를 참고하세요. 문법 검사와 실행 중인 서버의 상태 확인은 다음 명령을 사용하세요.

대용량 이미지 경로는 서버 실행 후 `/assets/image-worker-benchmark.html`에서 확인할 수 있습니다. 이 자동 fixture는 Worker에서 12MP JPEG 변환, 100ms 이상 Long Task, 재선택 취소, 과도한 픽셀 헤더 거부를 한 번에 검사합니다. Worker 기능이 없는 브라우저는 12MP의 더 낮은 fallback 상한을 적용합니다.

```bash
python -m compileall -q src/colorless
curl http://localhost:8765/health
curl http://localhost:8765/ready
curl http://localhost:8765/metrics
```

프런트엔드 소스를 바꾼 뒤 `frontend`에서 `npm run build`를 실행해 번들과 HTML fingerprint를 함께 갱신해야 합니다. CI는 `npm run build:check`로 커밋된 산출물이 소스와 정확히 일치하는지 확인합니다. 모바일 화면, 로그인, 친구 추가, 채팅 전송, 첨부 업로드도 브라우저에서 직접 확인해야 합니다.

## 알려진 제한 사항

- 휴대폰 인증은 개발용 코드 미리보기만 구현되어 있습니다.
- 로컬 SQLite 모드는 단일 서버 프로세스용입니다. 여러 인스턴스를 운영하려면 Supabase 같은 공유 저장소가 필요합니다.
- 세션 토큰 해시는 상태 저장소에 보관됩니다. 실시간 접속 상태만 서버 재시작 시 초기화됩니다.
- `StateStore`는 독립 모듈이지만 여전히 큰 단위이며 `ChatHandler`도 여러 기능 라우트를 포함합니다. 다음 단계에서는 두 클래스를 기능별 서비스와 HTTP route mixin으로 더 세분화해야 합니다.

## 기여

작은 단위로 변경하고, 커밋에 비밀 값이나 런타임 데이터를 포함하지 마세요. Pull Request에는 변경 이유와 직접 확인한 흐름을 적어 주세요.

## 라이선스

이 저장소에는 아직 라이선스가 지정되어 있지 않습니다. 재사용이나 배포 권한이 필요하면 저장소 소유자에게 먼저 확인하세요.
