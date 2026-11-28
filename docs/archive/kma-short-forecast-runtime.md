# KMA 단기예보 run 시간과 상한(2026-11-24)

수집 실패 감사(Map `docs/reports/2026-11-23-collection-failure-audit.md` §2.2, "`kma_short_forecast_job`
중앙값 8,166초, 상한 10,800초, 취소 10건")의 후속 검토다. 운영 Dagster storage를 읽기 전용으로 조회했다
(odroid 공용 instance, 2026-11-09 ~ 11-23, 14일).

## 실측

| 구분 | 건수 | 값 |
|---|---|---|
| 성공 | 75 | step p50 8,165초 · p90 9,665초 · 최대 10,736초 (run p50 8,494초) |
| 실패 | 11 | 상한 회수(`DagsterExecutionInterruptedError`, run 11,013~11,429초) 6 · 11-19 `KMA_COLLECTION_FAILED`(RuntimeError, `publish_ms` 10,808초·완료 208/300격자 — 상한 시각의 중단으로 보임) 1 · `KmaServerError`(HTTP 500) 3(11-10·11·13, 격자 격리 #241 이전, 이후 재발 없음) · 그 밖 1 |
| 취소 | 10 | 전부 "forcibly marked as canceled"(배포·수동 정리). run 423~9,576초로 상한과 무관. 11-17~18(디스크 가득·PG 복구 주간)에 6건 |

성공 run의 단계 시간(최근 14회, step 출력 metadata):

| 단계 | 값 | 비고 |
|---|---|---|
| `publish_ms` | p50 7,347초(75회), 최근 14회 5,539~7,881초 | fact 게시(`timed_publish` = `ingest_fanout` → `ingest_batch`). 384~441천 fact/run |
| `normalize_ms` | p50 555초 | prefetch(동시 4)와 겹친 누적값 |
| `fetch_ms` | p50 822초 | 300요청, prefetch와 겹친 누적값 |

step wall의 약 90%(p50 기준)가 게시다. 같은 기간 초단기예보는 step 961초 중 게시 883초, 초단기실황은
447초 중 402초로 KMA 세 job 모두 게시가 지배한다. 정규화는 로컬 측정으로도 격자 하나(864행)에 약
50ms라 원인이 아니다. 요청 속도는 300요청/8,165초 ≈ 0.04 req/s로 `KMA_MAX_RPS`(운영 10)의 토큰
버킷은 사실상 대기가 없다 — rate limit을 올려도 줄지 않는다. sync run lease(3시간)는 격자·chunk마다
갱신하는 heartbeat 기준이라 4시간 run도 lease를 잃지 않는다.
게시는 300격자 × 격자당 위치 fan-out(평균 약 1.4위치) × 약 940 fact를 partitioned fact 표와 최신 포인터에
쓰는 일이다. 11-20 변경(최신 포인터 FK의 generic plan, journal 2026-11-20)이 배포된 11-20~22의 평균 run은
약 7,000~7,150초로 이전 주(8,700~9,500초)보다 줄었지만, 11-23 08:15Z 회차는 다시 상한에 회수됐다.

## 결정

- 수집 경로에서 "확실히 줄일 수 있는" 비용은 찾지 못했다. 남은 비용은 위치별 fact 저장 설계(fan-out)와
  호스트 IO·CPU 포화이며, 이것을 바꾸는 일은 저장 모델 변경이라 이 후속의 범위가 아니다.
- 상한을 10,800 → 14,400초(4시간)로 올린다. 근거:
  - 상한 회수 6건(+11-19의 상한 시각 중단 1건)은 3시간 일을 끝까지 하고도 승격 없이 버렸다. 그 뒤 다음 회차가 처음부터 다시 해서
    최신 단기예보가 5시간 이상 늦었다. 4시간이면 넘긴 회차가 3시간 조금 넘어 끝나고 바로 게시된다.
  - 겹침은 없다. schedule은 coalescing이라 진행 중이면 다음 tick을 합치고(`tests/test_definitions.py`가
    고정), 작업별 동시 1 제한(`kortravelcommon/job`)도 그대로다.
  - 대가: 넘긴 회차가 끝날 때까지 다음 발표 회차(3시간 뒤 tick)는 건너뛴다. 3시간 안에 끝나는 보통
    회차에는 영향이 없다. 진짜 멈춘 run은 1시간 늦게 회수된다.
- 재검토 조건: 성공 p90이 3시간을 넘거나 상한 회수가 다시 주 1회를 넘으면 fact fan-out·게시 경로의
  설계 검토로 넘어간다.
