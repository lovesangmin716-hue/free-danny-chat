# 스레드 — issue #25

로그인 후 **MY → 스레드 · 게시물과 알림**에서 이용합니다. 별도 가입이나 OAuth 설정은 필요하지 않습니다.

## 구현 범위

- 통합/선택 ID별 전체·팔로잉 피드, 내용 검색, 커서 기반 더 보기
- 텍스트 게시물(2,000자), 댓글·답글, 작성 ID만 수정/소프트 삭제
- 전체 공개/팔로워 공개: 답글은 원문의 공개 범위를 따릅니다.
- ID별 좋아요·팔로우·차단 및 관리 화면, 정확한 `@사용자명` 검색
- `@사용자명` 멘션, 답글·좋아요·팔로우 알림, 통합/ID별 알림함과 독립 읽음 상태
- ID와 답글 대상별 로컬 초안, 요청 재시도의 중복 게시 방지, 전송 중 수정한 초안 보존
- 신고 사유 저장: 해당 활동 ID로 신고하며 운영자만 DB에서 조회할 수 있습니다.

카드마다 실제 행동할 ID를 표시합니다. 통합 피드에서 선택 ID에 열람 권한이 없는 글은 열람 가능한 본인 ID로 표시/행동합니다. 다른 계정에는 작성자의 다른 소유 ID나 계정 연결이 노출되지 않습니다. 편집기는 항상 작성 ID를 표시하고, ID 전환 전 초안을 저장합니다. 팔로워 공개 글의 멘션은 수신 ID에도 열람 권한이 있을 때만 전달됩니다.

차단은 두 ID 사이에서 양방향 열람·반응·팔로우를 막고 기존 양방향 팔로우를 해제합니다. 다른 소유 ID로 전파되지 않습니다. 통합 피드는 차단하지 않은 다른 본인 ID에서 보이는 글을 계속 보여줄 수 있습니다. 차단을 해제해도 팔로우가 자동 복구되지는 않습니다. 계정 제재는 기존 `accounts.status` 정책대로 모든 소유 ID에 적용합니다.

삭제는 본문을 비우는 소프트 삭제입니다. 기존 답글의 연결과 작성 ID를 보존합니다. ID 비활성화도 기존 게시물을 지우지 않지만 새로운 활동과 비활성 ID를 이용한 통합 열람을 거부합니다. 신고 후 삭제된 본문은 신고 테이블에 별도 사본으로 보관하지 않습니다.

## 운영 배포 — 반드시 먼저 DB 적용

1. Supabase 프로젝트의 Database 백업 정책을 확인하고 필요한 백업을 확보합니다.
2. Supabase **SQL Editor → New query**에서 기존 `src/colorless/database/supabase-schema.sql`이 적용되어 있는지 확인합니다. 신규 DB는 먼저 이 파일을 실행합니다.
3. 같은 SQL Editor에서 **`src/colorless/database/threads-schema.sql` 전체를 실행**합니다. 반복 실행 가능하며 기존 채팅 테이블/데이터는 삭제하지 않습니다.
4. Render 서비스의 빌드에서 `python tests/deploy_preflight.py --environment --remote`가 통과하는지 확인합니다. 이 검사는 스레드 테이블·뷰·트랜잭션 RPC를 검증하며 테스트 글은 생성하지 않습니다. 기존 `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`를 사용하며 새 환경 변수는 없습니다.
5. 이 브랜치/PR 병합 후 Render에서 최신 커밋을 배포합니다. 두 도메인이 같은 Render 서비스로 연결된다면 동일하게 적용됩니다. 별도 서비스라면 각각 같은 버전을 배포해야 합니다.
6. 실제 계정으로 MY의 스레드를 열어 글·답글·ID 전환·알림을 확인합니다. 키나 로그인 토큰을 이슈/PR에 붙이지 마세요.

SQLite 개발 환경은 시작할 때 추가 스키마가 자동 생성됩니다. 운영 SQL 적용과 Render 재배포는 코드 변경만으로 수행되지 않습니다. 애플리케이션 롤백 시 스레드 테이블을 삭제하지 마세요.

## 저장 및 권한

`thread_posts`, `thread_edges`, `thread_likes`, `thread_notifications`, `thread_reports`는 모두 `users.id`를 활동 주체로 참조합니다. `thread_meta`는 동시 변경 충돌 감지용 revision 하나를 보관하고 `thread_post_summary`는 반응/답글 수를 계산합니다. SQLite와 Supabase는 같은 도메인 검증 코드를 사용합니다.

브라우저는 DB에 직접 접근하지 않습니다. 테이블은 RLS 활성화 및 public/anon/authenticated 권한 회수, 서비스 역할만 접근하도록 설정합니다. `colorless_threads_commit` RPC 역시 서비스 역할만 실행 가능합니다. 서버가 세션 소유권과 글/관계 권한을 검증한 뒤 mutation을 보내며, DB는 트랜잭션 내에서 현재 활동 ID 상태를 재확인합니다. RPC는 임의 테이블명을 허용하지 않습니다.

모든 스레드 쓰기는 전역 revision CAS로 직렬화됩니다. 권한 계획 이후 다른 스레드 변경이 발생하면 최대 3번 재검증하며 실패 시 409로 안전하게 중단합니다. 이는 소규모 서비스의 정확성을 위한 설계이며 대규모 쓰기 부하에서는 ID/글 단위 잠금 방식으로 확장해야 합니다. ID 비활성화/계정 제재는 기존 DB 행 잠금과 연동합니다.

피드는 한 번에 최대 400개 후보를 검사해 20개를 반환합니다. 권한 때문에 빈 구간이 발생해도 커서가 전진합니다. 페이지별 작성자·관계·좋아요를 묶음 조회하고 HTTP 응답은 `no-store`입니다. 상세/관계/알림은 최대 40개씩 반환합니다. 알림함을 열어 둔 경우 유휴 상태에서 20초마다 갱신하며 서버 SSE에는 아직 연결하지 않습니다. 쓰기 제한은 ID당 게시 30회/분, 나머지 작업 60회/분이고 계정당 3배입니다. 기존 제한기처럼 인스턴스 로컬 제한입니다.

## API

공통 접두사: `/threads/api/`. 인증 쿠키와 `X-Acting-Identity`를 사용합니다. POST는 기존 `acting_identity_id` 본문 계약도 지원하며 헤더와 충돌하면 거부합니다.

| 메서드/경로 | 주요 입력 |
| --- | --- |
| GET feed | scope=all/identity, mode=public/following, q, cursor |
| GET detail | id, cursor |
| GET notifications | scope, cursor |
| GET people | q=정확한 사용자명 |
| GET relationships | kind=follow/block, cursor |
| POST create | body, clientId, parentId(선택), visibility=public/followers |
| POST edit/delete | id, body(edit) |
| POST like | id, enabled(boolean) |
| POST follow/block | identityId, enabled(boolean) |
| POST report | id, reason(5~500자) |
| POST read | id(알림 ID) |

## 테스트와 남은 범위

`python -m unittest discover -s tests -p 'test_*.py'`, `node tests/thread_drafts.mjs`, 프론트 빌드/구문/정적 예산 검사를 실행합니다. `tests/test_threads.py`는 실제 HTTP 소유권, 공개 범위, 독립 관계/반응/읽음, 차단, 멘션, 수정/삭제, 페이지네이션, 입력 검증, 비활성화, CAS 및 DB 어댑터를 검증합니다.

CI의 `tests/threads_postgres.mjs`는 임시 PGlite(PostgreSQL/WASM) DB에서 SQL 재실행, 모든 변경 테이블의 upsert/delete, 트랜잭션 롤백, revision 충돌, 계정 제재 및 anon/authenticated 권한 거부를 검증합니다. 운영 의존성에는 추가하지 않으며 실제 Supabase/PostgREST 연결 검증은 배포 전 remote preflight로 별도 수행합니다.

이번 버전은 텍스트 스레드입니다. 미디어 첨부, 추천 랭킹, 푸시 알림, 운영자 신고 검토 화면, 계정 완전 삭제/익명화는 포함하지 않습니다. 따라서 issue #25의 모든 장기 요구사항을 완료한 것으로 자동 종료하지 않습니다.
