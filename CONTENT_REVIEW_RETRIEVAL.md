# 격리 다국어 검색과 영속 색인

`review_embeddings.E5Encoder`와 `review_store.PersistentReviewSearch`를 별도 실험 경로로 추가했다. 서버 composition root의 `THREAD_CONTENT_REVIEW=None`은 유지한다. 키 입력·초안 저장·기존 채팅 전송에는 연결하지 않는다. 운영 DB 스키마/배포 설정은 변경하지 않는다.

## 실행과 재현

Python 3.11 이상 별도 venv에서 [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small)의 고정 ONNX/토크나이저 파일만 받는다. 모델 revision과 각 SHA-256은 `review_artifacts.E5_FILES`에 고정한다. 모델 카드의 MIT 조건을 확인했다. 실행 중 다운로드, PyTorch, 원격 Python 실행은 없다. [ONNX Runtime 공식 API](https://onnxruntime.ai/docs/api/python/api_summary.html)의 CPU provider를 사용한다.

```powershell
python -m pip install -r research/requirements-retrieval.txt
python -m colorless.review_artifacts .codex-review-model-data/e5 --model e5
python research/retrieval_smoke.py .codex-review-model-data/e5 new-retrieval-report.json
```

마지막 명령은 임시 SQLite Threads 저장소와 색인 파일을 만들고, 종료 시 정리한다. workspace `.env`/Supabase 키를 사용하지 않는다. 기존 보고서를 덮어쓰지 않는다. [실측 smoke 보고서](research/retrieval-smoke-report.json)는 실제 E5, 한·영 합성 문장, 임시 DB를 사용했다. 정답셋 품질·통합 모델·KAIST·HTTP·처리량을 측정했다고 주장하지 않는다.

## 임베딩과 긴 글

- 동일 의미/재작성 후보를 찾는 대칭 검색이므로 양쪽 모두 `query: ` 접두사를 사용한다. attention-mask 평균 pooling 후 384차원 L2 정규화를 적용한다. 패딩 토큰은 평균에서 제외한다.
- 512토큰 이내 본문 전체와 문단을 색인한다. 긴 본문은 384토큰 구간, 64토큰 겹침으로 분할하며 문단도 추가한다. 제목은 별도 구간이다. 긴 문서 전체를 하나의 벡터로 표현한다고 주장하지 않는다. 실제 inference 직전에 512토큰 이내인지 다시 검사한다.
- 원문은 변경하지 않고 `body:start:end` / `title:start:end` 구간 ID를 기록한다. 토크나이저 오프셋의 Unicode 문자 위치를 사용하고 내용/꼬리 누락을 검사한다. 검색 후보에는 잘린 조각 대신 원문 전체와 최상위 구간 ID를 전달한다.
- 요청은 최대 20,000자, 문서는 최대 128개 구간, 추론 batch는 최대 8개다. 범위를 넘기면 누락/자동 축약 대신 실패한다. query의 모든 구간과 문서 구간의 최대 cosine으로 문서별 후보를 병합한다. 높은 cosine을 위반 판정으로 변환하지 않는다.

## 영속 저장과 권한

공개 글은 `public.sqlite3`, 비공개 차단 기록은 `blocked.sqlite3`에 저장한다. 파일마다 scope, 모델 버전, 차원, 정책 버전, source revision, snapshot digest를 기록한다. 다른 설정으로 열면 실패하며 새 디렉터리에 재색인해야 한다. 공개 검색 `search(..., include_blocked=False)`가 기본이고 내부 검사 callable만 두 저장소를 사용한다. 새 HTTP 검색/API나 피드 경로는 추가하지 않는다.

권한 검사는 **최종 top-k를 자르기 전**에 적용한다. 내부 점수 순으로 접근 가능한 문서를 k개 모을 때까지 검사하므로 상위 비공개/차단 관계 문서가 유효 후보를 밀어내지 않는다. k개를 확보한 뒤 더 낮은 문서마다 DB 권한 조회를 하지 않는다. 현재 수정 중인 글도 최종 top-k 전에 제외한다. `ContentReview`가 `authorize` callback을 검색에 전달하고 반환된 공개 후보도 다시 검증한다. 비공개 차단 기록은 이 callback으로 공개 권한을 흉내 내지 않고 별도 내부 capability에서만 조회한다.

현재 HTTP 연결의 권한 검증은 authoritative Threads DB의 원문/삭제/공개 범위/루트 공개 범위/차단 관계를 사용한다. 외부 기사는 이 경로에서 계속 거부한다. `review_snapshot()`은 팔로워 공개 글과 그 하위 글, 삭제된 글 및 삭제된 루트 아래 글을 공유 색인에서 제외한다. 스냅샷에 계정/작성 ID 필드를 내보내지 않는다.

## 원자적 갱신과 늦은 작업

`ThreadRepository.review_snapshot()`은 keyset pagination으로 전체를 읽고 시작/끝 global revision을 대조한다. 기본 10,000건 한도를 넘으면 실패하며 일부 문서를 전체인 것처럼 게시하지 않는다. 이 한도는 공개 글로 거르기 전 원본 행에도 적용한다.

`refresh_threads()`는 이 스냅샷으로 새 벡터를 준비한 뒤 한 트랜잭션에서 교체한다. 추론 중 DB 쓰기 transaction을 잡지 않는다. 기존 제목/본문 digest가 같으면 영속 벡터를 재사용하므로 좋아요/읽음/시각 변경에 재임베딩하지 않는다. 수정은 해당 글만 재임베딩하며 삭제/공개 범위 축소/루트 삭제는 다음 완전한 스냅샷에서 제거한다.

작성 중 source revision 변경, 오래된/reused revision, 다른 builder의 교체, 모델 오류, 크기 초과, timeout은 이전 색인을 보존한다. 검색은 추론 전에 watermark를 확인하고 검색 snapshot과 종료 시점에도 revision을 확인한다. 늦은 색인을 '중복 없음'으로 취급하지 않는다. 글로벌 revision은 비콘텐츠 쓰기에도 증가하므로 이 격리 구현은 안전하게 자주 보류할 수 있다. 성공한 게시 후에도 다음 검사 전 refresh가 필요하다. 자동 운영 worker나 outbox를 설치한 것은 아니다.

문서 수와 별도로 전체 벡터 수도 제한한다. namespace당 최대 20,000개이면서 float 성분 합계 4,000,000개 이하이며, E5 384차원에서는 최대 10,416개 벡터다. 긴 글의 많은 구간으로 메모리가 무한히 커지지 않으며 초과한 스냅샷은 기존 색인을 보존한 채 거부한다.

차단 기록은 검수된 전체 감사 스냅샷을 `search.blocked.replace(records, revision=...)`에 명시적으로 제공해야 한다. 새 저장소를 자동으로 '차단 이력 없음'으로 간주하지 않는다. 비공개 색인 미초기화도 검사 보류 사유다. 거부된 사용자 글을 자동 저장하거나 합성 자료를 실제 차단 이력으로 만들지 않는다.

차단 번복/정책 변경은 감사 스냅샷 교체 또는 새 namespace 재구축으로 처리한다. 만료 항목은 즉시 검색에서 제외하며 격리 유지보수 worker가 `search.blocked.purge_expired()`를 호출해 제거한다. 비공개 SQLite는 `secure_delete=ON`을 사용한다. 이 동작은 서버 게시글/알림 DB를 수정하지 않는다. 별도 백업/이전 namespace의 수명 관리까지 자동 해결하는 것은 아니다.

## 지연과 캐시

기본 query 캐시는 원문/후보/권한/판정 대신 **내용+모델 버전으로 계산한 임베딩만** 300초 보관한다. 최대 64개 항목이자 총 256개 구간으로 제한한다. LRU/만료를 적용하고 락 안에서 추론하지 않는다. `query_cache_size=0`으로 비활성화할 수 있다. 같은 본문의 멱등 재시도/CAS 재시도에서도 검색·현재 권한·revision·통합 모델 판정은 다시 수행한다. 활동 ID의 노출 이력을 저장하거나 계정별 이력을 합치는 기능은 없다.

검색 후보와 판정을 재사용하지 않는 캐시 검증, 만료·벡터 재사용, 저장 후 재시작, 동시 builder, 삭제·루트 권한 변경·색인 누락/지연은 회귀 테스트로 검증한다. smoke 보고서의 미캐시/캐시 p95는 소수 합성 문장을 사용하는 로컬 CPU 측정이며 운영 SLO가 아니다.

## 운영 전 남은 범위

이 구현은 소규모 **exact cosine** 기반이다. 운영 ANN/트랜잭션 outbox, corpus별 watermark·백업/복구·보존 정책, 다중 프로세스 worker, 기사 권한/수집과 본문 확보, 검수된 한·영 검색 품질과 실제 부하/비용 평가는 남아 있다. 데이터가 없는데 full policy evaluator를 통과시킨 것처럼 취급하지 않는다. 기본 비활성 원칙 아래 격리 서버에서 이 검색을 통합 모델/검증된 KAIST 어댑터에 명시적으로 조합할 수 있도록 준비했다.
