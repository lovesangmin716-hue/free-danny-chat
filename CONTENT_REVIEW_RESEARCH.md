# #33 공개 연구·데이터 조사와 로컬 재현

2026-10-21 확인. 이 문서는 공개 자료의 목적과 재현 범위를 기록한다. 연구의 원래 라벨을 서비스 정책의 위반 라벨로 자동 변환하지 않는다.

## KAIST 연구 특정

가장 가까운 후보는 Kim et al., **Cross-National Information Attacks: A Two-Decade Analysis of Troll Behavior in Korea** (USENIX Security 2026)다. 이슈가 논문 이름을 지정하지 않았으므로 동일 논문이라고 확정하지는 않는다.

- [논문](https://arxiv.org/abs/2606.22785), [KAIST 공식 소개](https://www.kaist.ac.kr/newsen/html/news/?GotoPage=1&mng_no=65670&mode=V&sval=BEL), [저자 홈페이지](https://jaehong-k.github.io/).
- [저자 공개 자료 v2.1](https://zenodo.org/records/21348299), DOI `10.5281/zenodo.21348299`. 이전 v1에는 모델이 없었으나 v2.1 `code.zip`에는 모델 코드·가중치와 1,000건 추론 입력 샘플이 있다. Zenodo 메타데이터의 라이선스는 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- 공유 KcELECTRA 인코더와 18개 출력층: 이진 2개, 다중 라벨 3개, BIO 근거 구간 13개. [기반 모델](https://huggingface.co/beomi/KcELECTRA-base)은 MIT로 표시되어 있다.
- 네이버 한국어 뉴스 댓글의 외국 연계 의심·도덕적 감정·대상 국가를 연구한다. 정치 비판/부정적 감정만으로 일반 커뮤니티의 분란 조장을 확정하는 모델이 아니다. 국적·출신·거주 지역 추정이나 모델의 troll 점수를 계정 제재/게시 차단 조건으로 가져오지 않는다.
- 논문의 전체 수집 댓글과 모델이 발견한 계정은 사람이 검수한 위반 정답이 아니다. 논문은 사람 검수 1,452건 중 1,000건을 test로 사용하고, 별도의 약 5만 건 LLM 라벨로 경량 모델을 학습한다. 공개 `sample.parquet`에는 입력만 있으므로 정답 평가셋으로 보고하지 않는다.

## 실제 파일과 실행 방법

`review_artifacts`는 Zenodo v2.1의 고정된 ZIP 바이트 구간에서 체크포인트만 해제한다. 압축 크기 402,713,466바이트 / 해제 크기 434,203,698바이트다. 2.82GB `code.zip` 전체나 21.22GB `data.zip`을 받지 않는다. 서버가 Range를 무시하면 실패한다. 작은 구간별 전송, 출력 크기 제한, SHA-256 확인, 임시 파일 후 원자적 교체를 적용한다. 이미 있는 파일도 재검증하며 다른 내용이면 덮어쓰지 않는다.

기반 config/tokenizer는 Hugging Face revision `681201a097b8120e6dd2a2e63a3e13d338736f88`로 고정하고 파일별 SHA-256을 확인한다. 체크포인트에 인코더 전체가 있으므로 기반 모델 가중치를 중복 다운로드하지 않는다. 다운로드된 Python 코드를 실행하지 않으며 `trust_remote_code=False`, `local_files_only=True`, `torch.load(weights_only=True, map_location="cpu")`, strict state loading을 사용한다.

실험 환경은 Python 3.11 별도 venv를 권장한다. 서버 기본 dependencies에는 추가하지 않는다. PyTorch CPU 패키지는 [공식 설치 경로](https://pytorch.org/get-started/locally/)를 사용한다.

```powershell
python -m pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r research/requirements-kaist.txt
python -m colorless.review_artifacts .codex-review-model-data/kaist
python -m colorless.kaist_research .codex-review-model-data/kaist input.jsonl output.jsonl --threads 2
```

입력 JSONL은 `{"id":"sample-1","text":"검토할 원문"}` 형태다. 논문의 원래 입력은 마스킹된 사용자·작성 지역·기사 제목·댓글을 포함한 `full_text`이다. 이 runner는 사용자 메타데이터를 추정하거나 만들어 넣지 않는다. 일반 스레드 본문만 입력하면 원 연구와 다른 분포라는 점을 유지한다. 영어 일반 글에서의 품질도 검증되지 않았다.

출력은 원래 head별 연속 확률과 원문 Unicode 구간, 입력 SHA-256, 토큰 수, 로컬 측정 시간을 담는다. `research_only=true`, `moderation_validated=false`이며 allow/block이나 검증된 분란 조장 라벨을 생성하지 않는다. 원 논문의 임계값을 서비스 차단 임계값으로 적용하지 않는다. 512토큰을 넘으면 자동 잘라내기 대신 오류를 반환한다. 서버는 이 모듈을 import하지 않는다. 출력에는 근거 원문이 들어가므로 로컬 실험 디렉터리에서만 관리하고 저장소에 올리지 않는다.

실제 가중치 smoke test는 공개 입력 첫 20건과 한·영·이모지 동작 확인용 3건을 사용한다. 정답은 없으며 품질 지표를 생성하지 않는다. 다음 명령은 공개 샘플 파일 140,851바이트만 추가로 확보해 해시를 확인하고, 18개 head·확률 범위·정확한 원문 구간·잘못된/긴 입력 거부를 검증한다. 기존 보고서는 덮어쓰지 않는다.

```powershell
python -m pip install polars==2.0.0
python research/kaist_smoke.py .codex-review-model-data/kaist new-smoke-report.json
```

이 작업 환경에서 실행한 결과는 [로컬 smoke 보고서](research/kaist-smoke-report.json)에 있다. 로딩 시간에는 모델 파일 해시 검증과 초기화가 포함된다. 개별 추론 시간은 토크나이즈·모델·근거 직렬화를 포함하며 로딩·HTTP·검색·통합 모델·DB 저장 시간은 제외한다. 작은 표본의 CPU 동작 확인이므로 운영 p95·처리량·오차단율 또는 영어 품질의 근거로 사용하지 않는다.

## 한·영 데이터 후보와 사용 경계

| 자료 | 공개 내용·조건 | 이번 작업에서의 용도와 한계 |
| --- | --- | --- |
| [PAWS / PAWS-X](https://github.com/google-research-datasets/paws), [설명·사용 조건](https://raw.githubusercontent.com/google-research-datasets/paws/master/pawsx/README.md) | 영어 paraphrase 및 한국어 번역. 한국어 train 49,401쌍은 기계 번역, dev 1,965 / test 1,972쌍은 사람 번역. 제공 조건은 모든 목적의 사용을 허용 | 의미가 가까워도 뜻이 다른 hard negative와 한·영 검색 평가 후보. paraphrase=정책 위반으로 변환하지 않음. dev/test는 문장쌍이 다르지만 sentence1을 공유할 수 있으므로 원문/번역/공유 문장 연결 군집을 다시 검사해야 함. NS placeholder 정제 필요 |
| [KLUE](https://github.com/KLUE-benchmark/KLUE), [라이선스](https://github.com/KLUE-benchmark/KLUE/blob/main/License.md) | 한국어 STS/NLI 등, CC BY-SA 4.0 | 유사도/함의 보조 평가. 기사 왜곡·새 정보·정당한 인용·차단 재게시 정답은 아님. 재배포/파생 데이터에는 해당 조건 보존 |
| [Detecting Incongruity, AAAI 2019](https://github.com/david-yoon/detecting-incongruity), [논문](https://ojs.aaai.org/index.php/AAAI/article/view/3756) | 제목-본문 불일치 연구 코드와 전처리 데이터 다운로드 안내. 저장소 코드 MIT, 오래된 Python 2 / TensorFlow 1 구현 | 제목-본문 비교의 보조 자료 후보. 코드 라이선스로 기사 원문 재수집·재배포 권한을 가정하지 않음. 문단 교체 등의 불일치가 드라마/실제 사건 오인·풍자/비판 인용 정답을 대신하지 않음 |
| [ELF22](https://github.com/huijelee/ELF22), [논문](https://aclanthology.org/2022.lrec-1.378/) | 영어 Reddit 맥락과 troll-response pairs, overt/covert 유형 및 대응 전략. CC BY-NC-SA 4.0 표시 | KAIST 관련 대안 연구로 확인. 한국어·영어 일반 글의 안전/분란 binary 학습셋이 아님. 비상업 조건 때문에 운영 학습 데이터로 자동 편입하지 않음 |
| [Moral-Emotion](https://github.com/Paul-scpark/Moral-Emotion) | 한·영 도덕 감정 데이터/모델과 별도 LLM 주석 데이터 안내 | 감정과 분란을 동일시하지 않음. 명확한 해당 파일의 사용 조건을 확인하기 전 복제/학습에 편입하지 않음 |

공개 자료를 합쳐도 #33의 세 검사를 모두 만족하는 한·영/혼용 **근거 구간까지 검수된 정답셋**이 되지는 않는다. 후속 데이터는 원 출처·허용 조건·기계/사람 주석을 보존한 수집 후보 큐에서 두 검수자와 조정 과정을 거쳐야 한다. 기존 `review_evaluation`의 `human_reviewed` 요구를 낮추거나, 검수하지 않은 데이터를 그렇게 표기하지 않는다. 차단 재게시 양성은 현행 정책으로 확정된 차단 기록이 필요하며 다른 데이터의 부정적 문장을 차단 이력으로 꾸미지 않는다.

## 다국어 검색 기준 모델

[multilingual-e5-small 모델 카드](https://huggingface.co/intfloat/multilingual-e5-small)는 MIT, 384차원이며 고정 revision 후보는 `614241f622f53c4eeff9890bdc4f31cfecc418b3`이다. 다국어 공통 벡터 공간의 첫 기준 모델로 선정하되 아직 운영 검색에 연결하지 않았다. 대칭 유사도에는 양쪽 `query: ` 접두사, 비대칭 검색에는 query/passage 접두사, attention-mask 평균 pooling과 L2 정규화를 사용한다. 긴 본문은 문단/구간별로 처리하며 잘린 기사만 보고 전체가 중복이라고 판단하지 않는다. 높은 cosine 하나로 게시 위반을 결정하지 않는다.

ANN·영속 검색 저장소·삭제 동기화와 세 검사 모델 학습은 별도 남은 작업이다. 공개 가중치를 찾고 실행하는 것과 현재 서비스 정책의 정확도·오차단율·운영 처리량을 검증하는 것은 각각 확인한다.

## 출처 표시와 변경

`kaist_research.py`의 호환 모델 구조는 Jaehong Kim, Hyeonseung Kim, Jiseon Kim, Alice Oh, Thorsten Holz, Wonjae Lee, Meeyoung Cha의 Zenodo v2.1 `code/model.py`를 참고해 재작성했다. [원 자료](https://doi.org/10.5281/zenodo.21348299), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). 변경 사항: 동적 head 등록, 로컬 config로 생성 후 체크포인트 전체 로딩, CPU 추론, 정확한 Unicode 근거 직렬화, 긴 입력 거부, 정책 판정 없는 연구용 출력. 저자들이 이 변경이나 서비스 이용을 보증한다는 의미는 아니다.
