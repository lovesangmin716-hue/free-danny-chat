# Colorless operations

이 문서는 운영 SLO, probe 의미, 로그·메트릭 필드, 경보와 재현 가능한 부하/장애 시나리오의 기준입니다.

## Service objectives

Render free/small 단일 인스턴스의 목표는 동시 실시간 사용자 25명입니다. 프로세스당 SSE 32개, 전체 요청 스레드 64개, 동시 body reader 16개를 상한으로 두어 재연결과 일반 API용 여유를 남깁니다.

| SLI | SLO | 측정 위치 |
| --- | --- | --- |
| 일반 API 가용성 | 30일 99.9% 이상 | `requests.server_error_rate`, route error rate |
| 메시지 전송 | p95 300ms 이하 | `POST /messages` route latency |
| messenger 최초 응답 | p95 500ms 이하 | `GET /messenger` route latency |
| 실시간 이벤트 전달 | p95 1초 이하 | `sse.event_delivery_p95_ms`, end-to-end probe |
| 서버 오류 | 5xx 0.1% 이하 | 전체/route request counters |
| 느린 실시간 연결 | queue drop 0 | `sse.queue_drops_total` |

부하 게이트는 p50/p95/p99를 모두 기록하되 위 p95와 오류율을 pass/fail 기준으로 사용합니다. 배포 사양이 바뀌면 동일 fixture로 기준선을 다시 측정한 뒤 이 표와 CI 기준을 함께 변경합니다.

## Probes

- `GET /live`: 프로세스가 HTTP 요청을 처리할 수 있는지만 확인합니다. 외부 의존성 장애 중에도 200일 수 있습니다.
- `GET /ready`: DB 왕복과 migration marker, persistence revision/pending/error, durable-event outbox, 요청 스레드와 body-reader 포화를 확인합니다. 하나라도 실패하면 503이며 트래픽을 받지 않아야 합니다.
- `GET /metrics`: 요청 latency/error/bytes, SSE, persistence, Shorts 외부 API/quota/cache, process CPU/RSS/thread/FD와 가장 최근 readiness를 JSON으로 제공합니다.

Render의 health check는 `/ready`를 사용합니다. 장애 조사에서는 `/live`가 200이고 `/ready`가 503이면 프로세스 재시작보다 `checks`, `database.error`, persistence lag/error, outbox와 saturation을 먼저 확인합니다.

## Structured request log

모든 완료된 HTTP 요청은 한 줄 JSON으로 기록됩니다. `request_id`, method, query가 제거된 정규화 route, status, latency, request/response bytes와 SHA-256으로 가명화한 `user_id`가 포함되며 동일 request ID가 `X-Request-ID` 응답 헤더에도 전달됩니다.

비밀번호, 세션·업로드 token, OAuth code, query string, signed URL, 원문 사용자명, 메시지 본문과 요청/응답 body는 기록하지 않습니다. 클라이언트가 보낸 request ID는 제한된 안전 문자와 길이를 통과할 때만 재사용합니다. `tests/test_server.py`가 correlation과 민감정보 배제를 자동 검증합니다.

## Metrics dashboard

배포 환경의 JSON/로그 수집기에서 `/metrics`를 주기적으로 수집하고 다음 패널을 같은 dashboard에 둡니다.

| Panel | Source |
| --- | --- |
| API p50/p95/p99, max, 5xx rate, bytes | `requests.latency_ms`, `requests.routes.*` |
| Connection saturation | `requests.active/limits.max_request_threads`, `sse.active/limits.max_sse_connections` |
| Queue saturation | `requests.active_body_readers`, `runtime.max_subscriber_queue_fill_ratio`, queue drops/outbox |
| Database/persistence | `readiness.database.latency_ms`, `persistence.revision_lag/pending_parts/error` |
| External API | Shorts external calls, run latency, failure, quota, circuit, catalog hit/stale age |
| Process | CPU seconds, RSS, threads, open FDs |
| Upload traffic/failures | normalized upload route bytes/status/error rate |

SQLite와 현재 Supabase REST 구현에는 애플리케이션 소유 DB connection pool이 없으므로 pool utilization은 해당 없음으로 표시합니다. 대신 readiness DB 왕복 지연, 요청/본문 동시성, persistence lag를 포화 지표로 사용합니다.

## Alerts

- page: 5분 5xx 비율 1% 초과와 1시간 0.1% 초과가 동시에 발생
- page: `/ready`가 2분 연속 실패하거나 DB probe가 2초를 초과
- page: SSE queue drop 증가, event outbox 8,000 접근, persistence error 발생
- ticket: 메시지 p95 300ms 또는 messenger p95 500ms를 15분 초과
- ticket: request/SSE/body-reader 용량 80%, subscriber queue fill 80%, persistence lag 100 접근
- ticket: Shorts circuit open, 일일 quota 80%, stale catalog age 6시간 접근

## Versioned load and failure profiles

`tests/operations_load.py`는 외부 키 없이 격리된 실제 HTTP 서버와 두 사용자/1:1 방 fixture를 만들고 로그인, messenger/read, 동시 메시지, SSE fan-out, signed upload의 grant-transfer-complete, Shorts feed, DB 장애 readiness를 실행합니다.

```bash
python tests/operations_load.py --profile smoke
python tests/operations_load.py --profile load
python tests/operations_load.py --profile spike
python tests/operations_load.py --profile soak
python tests/operations_load.py --profile soak --duration-seconds 7200
```

기존 staging 계정에도 적용할 수 있습니다.

```bash
python tests/operations_load.py --profile load \
  --base-url http://127.0.0.1:8765 \
  --username USER --password PASSWORD --room-id ROOM_ID
```

외부 target은 파괴적인 dependency injection을 하지 않습니다. 장시간 SSE 유지/회수는 `tests/sse_load.py`, 다중 broker/인스턴스 종료·replay는 `tests/multi_instance.py`, 대규모 DB와 bootstrap은 각각 `tests/storage_scale.py`, `tests/bootstrap_scale.py`, 브라우저 Long Task/Shorts virtualization은 versioned browser fixture로 분리되어 있습니다.

CI는 매 push/PR에 `smoke`를 실행하며 message/read/realtime p95, 5xx, 예상 밖 4xx, upload, readiness, queue drop 또는 dependency-failure 차단이 기준을 넘으면 실패합니다. `load`, `spike`, `soak`는 staging 또는 정기 작업에서 같은 명령으로 실행하고 결과 JSON을 변경 전후 기록에 첨부합니다. `--duration-seconds 7200`은 요청 수 대신 2시간 soak를 실행하며 concurrency·SSE·요청 수 역시 CLI에서 덮어쓸 수 있습니다.

## Local baseline (2026-08-19)

Windows 로컬 smoke fixture는 CI 변동성을 줄이기 위해 API 동시 요청 1개와 별도 SSE 4개를 사용합니다. 24/24 API 응답에 성공했고 메시지 p50/p95/p99는 15.944/44.797/44.797ms, read는 8.554/56.885/56.885ms, end-to-end SSE는 42.273/42.538/42.538ms, upload 전체는 39.494ms였습니다. 주입한 DB 장애에서는 `/live` 200을 유지한 채 `/ready`가 503으로 차단되었습니다.

이 수치는 localhost 기준선이며 배포 환경 SLO 달성을 주장하는 값은 아닙니다. load/spike/soak 결과는 아래 표에 동일 장비에서 다시 실행한 수치를 기록합니다.

| Profile | Concurrency / requests / SSE | Result | Message p95 | Read p95 | Realtime p95 | 5xx |
| --- | --- | --- | --- | --- | --- | --- |
| smoke | 1 / 24 / 4 | pass | 44.797ms | 56.885ms | 42.538ms | 0 |
| load | 12 / 300 / 20 | availability pass, latency fail | 594.001ms | 796.657ms | 70.053ms | 0 |
| spike | 32 / 400 / 32 | availability pass, latency fail | 1,449.555ms | 1,531.125ms | 30.346ms | 0 |
| sampled soak | 12 / 6,000 / 20 | availability pass, latency fail | 937.262ms | 998.615ms | 25.130ms | 0 |

Sampled soak는 224.833초 동안 26.686 req/s로 6,000/6,000 응답에 성공했고 4xx/5xx/예외/queue drop이 모두 0이었습니다. 이 로컬 SQLite 기준선은 지속 가용성을 확인했지만 후보 API latency SLO는 만족하지 못했습니다. 운영 승인용 soak는 staging에서 `--duration-seconds 7200`으로 다시 실행해야 합니다.
