# Map 호출 속도 개선·무실패화 검토 (2026-11-24)

상태: **검토 문서**. 제품 코드 변경·운영 측정·배포는 포함하지 않는다. 기준 코드는
kor-travel-weather `origin/main@e18f809`, kor-travel-map `origin/main@4c4ca23c9`이다.
운영 호스트(n150·odroid)는 이번 조사에서 접속하지 못했으므로 수치는 모두 저장소에 남은
과거 실측 보고서를 인용한다. 인용 수치와 추정은 구분해 적는다.

## 0. 요약

| 질문 | 답 |
| --- | --- |
| Map 호출이 느린 근본 원인 | 트랜잭션 크기가 아니라 **같은 계산을 요청마다 반복**하는 구조다. 첫 페이지마다 request path에서 freeze(위치 약1,451·승인 범위 약3,306·manifest)를 하고, cursor 페이지마다 승인 선택을 다시 검산한다. 캐시는 전부 프로세스 메모리·60초다. |
| 실패의 근본 원인 | Map `weather_state_current` 14일 549 성공/110 실패(16.7%), 그중 **run 상한 480초 초과 99건**(성공 p95 419초). 위 반복 비용 + Weather export 슬롯 2개 경합 + 5초 문장 상한(57014 → 503). |
| 트랜잭션이 너무 커서 메모리·lock 문제? | **메모리 문제는 아니다**(수집은 50곳/5,000 fact 청크·COPY, export 본문 ≤4MiB·manifest ≤8MiB). 문제는 (a) freeze RR 트랜잭션이 30초 넘게 열려 5초 문장 상한에 반복해서 걸리고 xmin을 고정하는 것, (b) 야간 파티션 유지보수가 ACCESS EXCLUSIVE를 쥔 채 최대 5초 조회를 돌려 지도 읽기를 최대 약 8초 멈출 수 있는 것이다. |
| 초기 1회 느림은 prewarm으로? | **Map export 경로는 HTTP prewarm으로 거의 안 풀린다**(캐시 60초·프로세스 메모리, 예보 첫 페이지는 캐시를 강제로 우회). 해법은 공개(publish) 시점에 표준 프로필 페이지를 DB에 미리 만들어 두는 것(설계 P0-A, 미구현)이고, 배포 hook은 그 builder를 한 번 돌리는 것이다. 관리 지도 경로(`/v1/weather/*`)는 API lifespan warmup으로 첫 묶음 약10초(26→15초)를 줄일 수 있다. |
| 가장 먼저 할 것 | ① cursor 페이지 승인 재검산을 snapshot 단위로 묶기(빠른 개선) ② ETag를 내용 digest로 바꾸고 무거운 읽기 전에 비교 ③ P0-A(W2·W5 사전 구성) ④ Map current 상한·동시성 풀 조정. |

## 1. "Map 호출"의 범위

`docs/integration-map.md`는 "Map은 날씨를 읽지도 쓰지도 않는다(Map ADR-105)"고 적고 있으나,
현재 Map main에는 Weather service export를 읽어 Valkey에 싣는 Dagster job이 있다
(map:`packages/kor-travel-map-dagster/src/kortravelmap/dagster/weather_state.py`,
map:`src/kortravelmap/providers/kor_travel_weather_state.py`, map ADR-114·122). ADR-105는
Map DB에 날씨를 **저장**하지 않는다는 결정이고, Map이 Weather를 **호출**하지 않는다는 뜻은 아니다.
integration-map 표의 해당 행은 후속 문서 정리 대상이다(이 PR은 단일 문서 원칙으로 건드리지 않는다).
→ §9 정리 PR에서 `docs/integration-map.md`의 kor-travel-map 행을 고쳤다.

이 문서는 두 경로를 다룬다.

- **A. Map → Weather service export** (`/v1/service/exports/*`, 이 문서의 주 대상)
- **B. 관리 UI 날씨 지도 → BFF → `/v1/weather/*`** (같은 API 프로세스·DB를 공유하므로 함께 본다)

## 2. 경로 A: Map → Weather service export

### 2.1 Map이 한 번에 하는 호출

| 데이터셋 | cron(KST) | run 상한 | 요청 | 근거(map:) |
| --- | --- | --- | --- | --- |
| W1 locations | `37 4 * * *` | 900s | `limit=2000`, ETag | `weather_state.py:821` |
| W2 current | `25,40 * * * *` | 480s | `limit=2000`, ETag | `:825` |
| W3 ultra_short | `48 * * * *` | 1,800s | `limit=200`, since 증분 | `:829` |
| W3 short | `30 2-23/3 * * *` | 3,600s | 〃 | `:838` |
| W3 mid | `40 6,7,18,19 * * *` | 900s | 〃 | `:843` |
| W3 external | `55 */3 * * *` | 2,400s | 〃 | `:848` |
| W5 daily | `41 * * * *` | 2,700s | `days=14&limit=2000` | `:856`, `:685-694` |

- 첫 페이지에만 `If-None-Match`를 보내고 304면 기존 세대의 확인 시각만 갱신한다
  (`weather_state.py:560,569-575,1253-1266`). 예보 전량은 ETag를 보내지 않는다(`:1197-1204`).
- 503(`EXPORT_TEMPORARILY_UNAVAILABLE`·`EXPORT_NOT_READY`)은 같은 cursor로 최대 4회, 5·10·20초,
  `Retry-After` 우선, 순회 전체 대기 60초 이하(`:208-230,406-442`). 전송 오류·429·기타 5xx는
  2회·15초(`:359-384`). step 재시도 2회.
- 코드 기본 timeout은 read 60초·connect 4초(map:`src/kortravelmap/settings.py:761,772`).
  ADR-122는 운영 read timeout 300초라고 적는다. **운영 실효값은 확인하지 못했다.**
- Valkey는 `SET NX` lock → 500건 pipeline → meta → CAS 포인터 교체(map:`valkey_state.py:969-1097`).
  교체 실패는 감사 보고서의 실패 원인 상위에 없다.

### 2.2 Weather가 그 호출 하나에 쓰는 비용

- **첫 페이지 = request path freeze.** REPEATABLE READ 트랜잭션에서 문장마다
  `SET LOCAL statement_timeout = '5s'`(weather:`src/kortravelweather/export_snapshots.py:653-657`),
  트랜잭션 전체 상한은 없다. digest를 알려면 freeze를 끝까지 해야 같은 snapshot handle을
  재사용할 수 있다(`:788-807`).
- **cursor 페이지마다 승인 재검산.** 다시 RR 트랜잭션을 열어 roster·승인 선택을 확인한다
  (`export_snapshots.py:1167-1199`). 공용 선택 캐시는 남은 수명이 30초 이상일 때만 이어 쓴다
  (`routers/service_exports.py:52,439`).
- **예보 첫 페이지는 페이지 캐시를 강제로 우회**한다 — INM이 없으면 `force=True`
  (`service_exports.py:451-452`). Map은 예보에 INM을 보내지 않으므로 예보 첫 페이지는 항상 새로 만든다.
- **캐시는 모두 프로세스 메모리, 재시작하면 빈다.** 페이지 캐시 64MiB·16항목·≤60초
  (`export_page_cache.py:26-30`), 선택 캐시 60초·8항목·16MiB(`export_selection_cache.py:9-14`),
  snapshot 메타 60초, 복호 캐시 64MiB. DB에 남는 것은 snapshot handle(TTL 15분·최대 6시간)과
  heads·artifact·일별 요약뿐이다.
- **동시성.** export 슬롯 `CapacityLimiter(2)`(`app.py:166`), 같은 키 대기 최대 60초 후 503
  (`export_page_cache.py:139`), DB pool 10+10·대기 15초(`repository.py:1495-1497`). Map이 timeout으로
  끊어도 `to_thread` 작업은 취소되지 않아 슬롯을 계속 쥔다.
- **ETag가 304를 거의 못 만든다.** ETag가 `snapshot_id`에 묶여 handle TTL(15분)이 지나면 내용이
  같아도 바뀐다(map ADR-114:432-434). 매시 도는 job은 사실상 304를 못 받는다. 비교도 본문 읽기·검증
  뒤에 한다.
- uvicorn은 `--workers` 없이 1 프로세스(`compose.yaml:155`).

### 2.3 실측(저장소 보고서 인용, odroid)

| 측정 | 값 | 근거 |
| --- | --- | --- |
| current 첫 요청 | 52.4s | `docs/reports/2026-11-09-cold-export-read.md:30` |
| current 60초 만료 뒤 | 23.8s | 〃 |
| current 304 | 173ms | 〃 |
| daily 첫 요청 | 13.1s | 〃 |
| freeze 단계 | 31.5~37.8s | `2026-11-08-aggressive-export-read.md:5`, `2026-11-08-remaining-read-and-overlap.md:5` |
| 승인 선택 첫 읽기 / 재사용 | 22.19s / 0.64s | `2026-11-08-selection-cache-release.md:5` |
| short 예보 cold 전량 | 약 1,174s | map ADR-122:14 |

### 2.4 Map 쪽 실패 실측(map `docs/reports/2026-11-23-collection-failure-audit.md`)

| job | 14일 성공/실패 | 주된 실패 |
| --- | --- | --- |
| weather_state_current | 549/110 (16.7%) | run 상한 480초 초과 99건, 성공 p95 419초 |
| weather_state_forecast_ultra_short | 285/44 (13.4%) | 교체 거부 28, 상한 초과 13 |
| weather_state_forecast_short | 55/56 (50.5%) | limit 2000 페이지 ReadTimeout(이후 page 200으로 축소, 회복) |
| locations·mid·external·daily | 492/18 | 정상(stale 표시) |

추가로 Weather 측 원인으로 지목된 것: `weather_export_publisher` sensor가 60초 tick에 평균 36초·최대
49초 사용, `regional_weather_job` DNS 1회 오류로 24시간 미승격, weather code server gRPC 14일 25회 단절.
반대로 stale인데 200이면 Map은 SUCCESS로 끝난다(오래된 자료가 초록으로 보임).

### 2.5 개선안 (효과·비용 순)

| # | 쪽 | 변경 | 기대 효과 | 비용·위험 |
| --- | --- | --- | --- | --- |
| A1 | Weather | **cursor 페이지 승인 재검산을 snapshot 단위로 묶기.** 같은 `snapshot_id`+liveness token이면 첫 페이지의 proof를 이어 쓰고, 만료 시 전체 선택 대신 작은 상태 proof(head vector)만 갱신. 60초 계약은 유지 | current 순회 중 약20초 선택 재읽기 제거 → 상한 초과 99건의 주원인 제거(추정) | 작음. 승인 상태 재검증 의미를 바꾸지 않도록 head 비교로 한정 |
| A2 | Weather | **ETag를 내용 digest(manifest digest+page+상태 token)로**, 비교를 본문 읽기 전에 | locations·mid·external·변동 없는 daily가 ms 단위 304 | ETag 의미 변경 → Map 소비 계약 시험 필요 |
| A3 | Weather | **P0-A: 표준 프로필 페이지 사전 구성.** publisher 뒤 별도 유한 Dagster job(비대한 sensor에 넣지 않음)이 head vector·catalog revision 키로 manifest와 표준 페이지(W2 limit 2000, W5 days 14)를 DB에 저장. 요청은 상태 proof만 검산하고 envelope 결합 | 첫 페이지 30~55s → 1s 미만 목표(설계 목표, 미검증). **재시작 뒤 첫 호출 문제도 같이 사라짐** | 중간. 설계 `docs/design/weather-performance-use-cases-20261109.md` §P0-A의 handle 수명·GC 보호 조건 준수 |
| A4 | Weather | **freeze 트랜잭션 분리.** 선택용 짧은 RR + snapshot INSERT용 짧은 트랜잭션(+`lock_timeout`). freeze 예산 초과 시 503 대신 `_previous_success`를 stale로 반환하는 경로 확대(`export_snapshots.py:1014-1020`) | 57014→503 감소, xmin 고정 시간 단축 | stale 반환 범위는 소유자 결정(60초 연장 금지 원칙과 충돌하지 않게 `source_status=stale` 명시) |
| A5 | Weather | **export 첫 페이지 forecast `force` 재검토.** INM 없는 예보 첫 페이지도 head가 같으면 페이지 캐시 재사용 | 예보 run의 첫 페이지 반복 제거 | 작음. 강제 우회의 원래 이유(최신 승인 반영)를 head 비교로 대체 |
| A6 | Weather | **취소 전파.** Map 연결이 끊기면 다음 단계 전에 중단, 장기 SQL은 `pg_cancel_backend` | 슬롯 2개 낭비 제거 | 작음 |
| A7 | Map | current run 상한 480→900초(lock TTL 동반), 503/timeout이면 재시도 대신 이번 tick 건너뛰고 지난 세대 유지 + stale 나이 지표 | 사용자에게 보이는 실패 제거(Valkey는 지난 세대 유지) | Map 저장소 작업 |
| A8 | Map | weather job들을 하나의 동시성 풀로 묶기(:25 current, :41 daily, :48 ultra_short, :55 external 겹침), read timeout을 Weather 첫 페이지 실측(최대 76초)보다 길게 | Weather 슬롯 2개·60초 대기 한도 경합 503 감소 | Map 저장소 작업 |
| A9 | Map | stale 200을 SUCCESS가 아닌 경고 상태로 표시 | 관측성 | Map 저장소 작업 |

권하지 않는 것: export 캐시 TTL 연장(승인 60초 계약 위반), Valkey/Redis 공용 캐시 도입(새 장애면,
설계 §6.2), 배포 직후 HTTP로 export를 두드리는 prewarm(효과 60초).

## 3. 트랜잭션 크기·메모리·lock

### 3.1 문제없는 것

- 수집 트랜잭션: 최대 50곳/5,000 fact(dagster `chunked_publish.py:30-36`), KMA 격자 2,500건 단위,
  external 원천 25개/10MB 단위. fact는 1,000행 COPY(`repository.py:989-1022`), 현재값은 1,000행
  배열 `INSERT…ON CONFLICT` CAS(`:3206-3258`). 2026-10-02의 4시간 단일 트랜잭션은 이미 없다.
- provider HTTP 호출 중에는 트랜잭션이 열려 있지 않다(HTTP 후 `asyncio.to_thread(ingest_batch)`).
- 위치 lock 정렬 획득·`lock_timeout`·rollback 후 재시도(`repository.py:1658-1678,2740-2759`).
- 파티션 유지보수: FK `NOT VALID` 재생성으로 DETACH 스캔 회피, 검증은 SHARE UPDATE EXCLUSIVE
  (`partitions.py:245-269,716-730`). 단기예보 보존 정리는 1,000행 배치·`lock_timeout` 1초.
- export publisher lease는 트랜잭션 없이 세션 advisory lock. `REFRESH MATERIALIZED VIEW` 없음.

### 3.2 고칠 것

| # | 위치 | 문제 | 수정 |
| --- | --- | --- | --- |
| L1 | `repository.py:5598-5607` `_detach_expired` | `weather_values`·`weather_current_values` ACCESS EXCLUSIVE를 쥔 채 `_preserve_short_forecast_partition` EXISTS(상한 5초) 실행 → 지도 읽기 최대 5초 정지 | EXISTS를 lock **전에** 실행해 보존 대상이면 건너뛰고, lock 뒤에는 재확인만 `statement_timeout` 200~500ms |
| L2 | `repository.py:5609-5617`, `partitions.py:553-559` | detached drop이 `weather_locations`까지 ACCESS EXCLUSIVE로 잡은 뒤 5초 EXISTS | 떼어낸 테이블은 쓰기가 없으므로 검사를 lock 전으로 이동 |
| L3 | `partitions.py:144-171`, `repository.py:5319-5333` | DDL lock 대기(약3초×6회) 동안 뒤 reader가 줄 섬. `_await_quiet_tables`는 2회차부터만 | 첫 시도 전에도 quiet 대기, 기본 `lock_timeout` 300~500ms, 차단자가 autovacuum일 때만 3초. (`DETACH CONCURRENTLY`는 DEFAULT 파티션 때문에 불가 — 코드 주석 `partitions.py:539-548`) |
| L4 | `repository.py:5196-5247` | 현재값 포인터 정리·`PURGE_SOURCE_RECORDS_SQL`이 상한 없는 단일 DELETE. 밀린 뒤 첫 실행이면 수백만 행·WAL·bloat, 수집 CAS와 행 lock 경합 | 1,000~5,000행 keyset 배치·배치별 commit·`statement_timeout 10s`·`lock_timeout 1s` |
| L5 | `repository.py:2735-2738` | 수집 트랜잭션에 statement/idle 상한 없음 | `SET LOCAL statement_timeout='30s'`, writer role `idle_in_transaction_session_timeout='60s'`(Map 감사 §9도 서버 설정 부재 지적) |
| L6 | `repository.py:2013-2025` | 재수집 판정 select가 raw payload(최대 8MiB)를 defer하지 않고 identity map에 보관 | `options(defer(SourceRecordRow.payload))` |
| L7 | `alembic/env.py` | migration 기본 `lock_timeout` 없음 | `SET lock_timeout='5s'` 기본 + 재시도 헬퍼 표준화 |
| L8 | `export_snapshots.py:649-848` | freeze RR이 30초+ 열림, 5초 문장 상한 반복 초과(INSERT 57014), xmin 고정 | A4와 같은 분리 |

공개 읽기(`/v1/weather/*`)는 마감 기반 `statement_timeout`(15초, `read_limits.py:32-42`)이 있어
lock 대기열에 무한정 매달리지는 않지만, `lock_timeout`이 없어 15초를 다 쓰고 503이 된다.
공개 읽기에 `SET LOCAL lock_timeout='2s'`를 더하는 것을 권한다.

## 4. 초기 1회 지연과 prewarm

### 4.1 무엇이 차가운가

| 비용 | 경로 | 근거 |
| --- | --- | --- |
| 빈 DB pool(10+10, pre-ping) — 첫 요청들이 TCP·인증·catalog cache 비용 | A·B | `repository.py:1492-1501` |
| psycopg prepare는 연결마다 5회 뒤 — generic plan 전환 시점이 연결마다 다름 | A·B | `docs/weather-marker-generic-plan-performance.md` |
| SQLAlchemy 컴파일 캐시(프로세스별) | A·B | `repository.py:3904-3905` |
| 지연 import(`kortravelcommon.address_providers`) | B | `repository.py:1299,1383` |
| export 캐시 3종·복호 캐시(재시작 시 빈 상태) | A | §2.2 |
| BFF 세션 확인 첫 호출(5초 fail-closed) | B | `frontend/lib/session.ts:56-90` |
| PG 버퍼·OS 페이지 캐시 | A·B | 수집기가 밀어낼 수 있음(분리 측정 없음) |

실측: 관리 지도 마커 60곳 묶음 배포 직후 26.8/25.5/25.3s → 이어서 14.6/15.6/14.9s
(`docs/reports/2026-10-28-marker-alert-prefix.md:10-15`). 즉 **1회성 비용 약 10초, 데운 뒤에도 약 15초**.

### 4.2 지금 있는 것

없다. API에 lifespan/startup hook이 없고(`app.py:144-383`), `/health`는 DB를 쓰지 않는 고정 응답
(`app.py:234-236`, `docs/weather-health-bytecode.md:20-21`), compose healthcheck는 `start_period` 없이
그것만 본다(`compose.yaml:157-161`). `deploy.sh`는 `/version` commit만 확인한다(`:85-97`).
운영은 HAProxy·host network라 포트가 열리면 바로 트래픽이 들어온다.

### 4.3 제안

- **경로 A(Map export): HTTP prewarm 대신 사전 구성(A3).** 배포 hook에서는 A3 builder를 한 번 실행해
  현재 head의 표준 페이지가 DB에 있게 한다. builder는 head가 같으면 no-op이어야 한다.
  A3 전이라도 A1·A2가 들어가면 재시작 뒤 첫 순회 비용의 대부분(선택 재읽기·불필요한 본문 재구성)이 준다.
- **경로 B(관리 지도): API lifespan 백그라운드 warmup.** `create_app`에 lifespan을 두고 포트 열기를
  막지 않는 `asyncio.create_task`로 1회 실행. 설정 플래그(`KOR_TRAVEL_WEATHER_API_PREWARM_ENABLED`,
  production만 on).
  1. 연결 2~4개 + `SELECT 1`, 지연 import 로드
  2. 세션 폐기 확인 쿼리 1회(BFF fail-closed 보호)
  3. `region_locations()`(읽기 함수만; 쓰기 `refresh_region_locations` 금지)
  4. `list_locations(limit=1000)`·`count_locations`·`public_location_regions`
  5. 앞쪽 위치 60곳×최대 2묶음 `marker_values_many`(`marker_limiter` 공유)
  6. 위치 1곳 latest·forecast
  - 안전 장치: 모두 읽기 전용·동시성 1, 문장당 5초, 전체 30초 예산, 예외는 로그만, 사용자 요청이
    2건 이상 처리 중이면 나머지 생략. 지도 엔드포인트에는 결과 캐시가 없으므로 오래된 데이터를 내보낼
    위험은 없다.
  - `/health`는 그대로 두고 새 `/ready`(`warm: done|skipped|running`)를 추가. `deploy.sh`·Manager verify가
    최대 60초 확인하되 초과는 경고만(배포 실패로 만들지 않음). 실제 트래픽 게이트는 HAProxy/Manager 변경이
    필요하다. `/ready`는 API 표면 추가이므로 OpenAPI·TS 타입 갱신이 같이 필요하다.
  - 효과 상한: 첫 묶음 약10초(실측 차이). 남는 약15초는 §5의 쿼리·직렬화 개선 몫이다.
- **pg_prewarm은 선택 사항.** 확장 설치·`shared_preload_libraries` 변경이 필요하고 DB가 여러 테넌트 공유라
  기본안에서 뺀다. 위 대표 쿼리가 같은 페이지를 대부분 데운다.
- **web:** `/login` GET 1~2회로 Next route 모듈·middleware 로드(자격증명 없이). web 컨테이너 healthcheck 추가.

## 5. 기타: 관리 지도(경로 B) 속도·무실패화

| # | 변경 | 근거 | 효과 |
| --- | --- | --- | --- |
| B1 | BFF 세션 확인 결과를 `sid`별 5~15초 메모리 캐시 + 동시 검사 합치기, 로그아웃 시 삭제 | 모든 요청이 `POST /v1/admin/session-revocations/check` → `auth_limiter = CapacityLimiter(2)`(`app.py:173`) | 호출마다 1 RTT·503 제거. 다른 replica 반영은 TTL만큼 늦음(소유자 결정) |
| B2 | middleware matcher에서 `/maplibre/` 등 public 정적 경로 제외 | `middleware.ts:105` | 인증 저장소 지연 시 타일 없는 지도 방지 |
| B3 | 응답 변환·직렬화를 worker thread로, `TypeAdapter.dump_json()` bytes 반환 | 모든 handler가 async이고 `model_dump`·재검증이 event loop에서 실행(`routers/weather.py:974-1001` 등), worker 1개 | 큰 응답 중 프로세스 전체 정지 제거 |
| B4 | 공개 경로 DB 연결 단절(08xxx·57P01)도 503+Retry-After로 | `app.py:194-208`은 PoolTimeout·57014만 503, 나머지 500 | 재시도 가능한 실패로 분류 |
| B5 | GET 기본 timeout(BFF 15s → 504, 브라우저 `AbortSignal.timeout(20s)`)과 502/503/504·네트워크 오류 2회 재시도(backoff·jitter·`Retry-After`) | `lib/api.ts:164-173`, BFF timeout은 locations·credentials만(`route.ts:7-11`) | 무한 대기·일시 장애 노출 제거 |
| B6 | 마커 `failed`를 영구 차단에서 시간 기반 재시도로 | `weather-map.tsx:1061,1065` | 일부 위치 영구 공백 제거 |
| B7 | 초기 viewport를 `initialCamera`로 계산해 마커 요청을 타일 `load`와 병렬로 | `weather-map.tsx:644,1692-1696` | 첫 마커 표시 앞당김 |
| B8 | 마커 compact DTO + gzip(worker thread에서) | 위치당 최대 80행×25필드, 배치 60곳(최대 4,800행), GZip 없음 | 전송·직렬화 감소(DTO 변경 → OpenAPI·TS 동반) |
| B9 | statement timeout을 bind 값(`set_config(...,true)`)으로 트랜잭션당 1회, handler당 세션 1개 | `read_limits.py:41-42`, `pool_pre_ping` | 문장당 왕복 감소, prepare LRU 오염 제거 |
| B10 | `/resolve` 후보 1만 행 ORM 로드를 SQL 필터로 | `routers/weather.py:750-759` | PinVi resolve 지연 감소 |
| B11 | marker limiter 대기에 `fail_after(3s)` → 503 | `routers/weather.py:889` | 15초 매달림 대신 빠른 실패·재시도 |
| B12 | 위치 카탈로그 첫 페이지 total로 나머지 병렬 수신 + TanStack Query 캐시, BFF가 `etag` 전달 | `lib/api.ts:217-223`, `route.ts:173-180` | 화면 진입·필터 변경 시 전량 재수신 제거 |

## 6. 진행 중인 PR과의 관계

2026-11-24 기준 열린 PR(다른 작업자)의 Map 호출·쓰기·cold start 영향이다. 이 문서는 docs 파일 하나만
추가하므로 코드·journal/resume 충돌은 없다. 다섯 Weather PR 사이에 코드 충돌은 없으나(`git merge-tree`
쌍별 확인) 모두 `docs/journal.md`·`docs/resume.md` 맨 위에 항목을 넣어 서로 rebase가 필요하다.

| PR | 변경 | Map 호출 영향 | 쓰기·lock·메모리 | 지적 |
| --- | --- | --- | --- | --- |
| weather #286 | KMA 단기예보 run 상한 3→4시간 | 상한에 걸려 버려지던 회차(14일 6건)가 게시되어 Map `forecast_short` 최대 지연 감소 | 게시 구간(38만~44만 fact, publish ≈83%)이 최대 1시간 늘어 Map export 읽기와 IO 겹침 증가. 트랜잭션 크기는 동일 | 없음 |
| weather #284 | sweep 없는 외부 schedule 12→6, 429는 다음 주기 | export 내용 불변 | 14일 약635 run worker spawn 제거 → odroid CPU 압박 완화(§2.4 원인에 직접 도움) | 429 시 아직 게시 전 batch(최대 5,000값)는 run `failed`로 버려지고 그 sweep cohort는 승격되지 않는다. PR 주석보다 범위가 넓으니 문서화 권장 |
| weather #283 | 지역 수집 원천별 job 4개 분리·일시 오류 재시도·보충 tick | 한 원천 실패가 나머지 승격을 막지 않음 → Map freshness 개선 | in-process run 4개가 겹쳐(산악 p50 646s) weather run 상한 6을 나눠 씀. 05·17시대 단기예보·외부 sweep과 겹치면 AirKorea·KMA run 대기 가능. 재시도는 트랜잭션 밖 | `transient_retry.py` docstring이 없는 `tests/test_transient_retry.py`를 가리킴 |
| weather #285 (draft) | 특보 한 요청 수집·충돌 행 격리·차단기 | PinVi·지도 특보 최신성 회복 | 게시는 짧은 트랜잭션 1개, 원천 payload 최대 8MiB | **위험:** `PAGE_SIZE = ALERT_MAX_ROWS`(10,000)로 요청하고 응답 `numOfRows`가 다르면 실패하는데, 실측 근거는 `numOfRows=1000`뿐이다. data.go.kr가 상한을 두면 5분 run이 전부 실패하고 차단기로 간격이 60분까지 늘어난다. 머지 전 10,000 요청 실측 또는 1000 유지 권장. schedule 평가마다 engine 생성·dispose |
| weather #282 (draft) | AirKorea 광주 통합 주소 측정소 13곳 누락 수정·partial | Map current에 측정소 13곳 추가 | 무시할 수준 | 광주·전남 두 bulk 묶음에서 이름만으로 매칭 → 같은 이름 전남 측정소가 active anchor에 없으면 광주 anchor에 잘못 붙을 수 있음(`raw.sidoName` 대조 권장, 낮음) |
| map #1541 | n150 Dagster freeze 도구·schedule 6개 | freeze 동안 `weather_state_*` 7개 정지, 놓친 tick 미보충 → 창이 끝난 뒤에도 다음 cron까지(최대 약 24시간) 날씨 상태가 낡는다. W1·W2·W5는 평소에도 매 순회 전량을 받으므로(§2.2, snapshot_id ETag는 304를 거의 못 만든다) "unfreeze 직후만 전량 fetch"는 아니다 | Weather DB와 무관 | **정정(2026-11-24):** 처음 "지적 없음"으로 적었으나 위 낡음과, 실패 1건을 처리한 SKIPPED tick을 따라잡음으로 세는 판정 결함이 있었다. map #1552에서 상태 schedule을 freeze 대상에서 빼고 판정을 고쳤다 |

이번 묶음에 Weather API prewarm·export 캐시를 바꾸는 PR은 없다. 위험이 작은 머지 순서는
#286 → #284 → #282 → #283 → #285(위 위험 해소 후)다.

## 7. 권장 실행 순서

| 순서 | 묶음 | 저장소 | 수용 기준 |
| --- | --- | --- | --- |
| 1 | A1·A5·A6 (빠른 개선) | weather | 로컬 시험 + odroid current 순회 wall·페이지별 시간 전후 비교, Map current 상한 초과 건수 7일 비교 |
| 2 | L1·L2·L3 (야간 lock) | weather | 야간 유지보수 중 지도 읽기 p95·lock 대기 지표 |
| 3 | B1·B2·B5·B4 (관리 지도 무실패화) | weather | 연속 선택·인증 저장소 지연 주입 시 사용자 오류 0 |
| 4 | A2 (내용 ETag) + Map 계약 시험 | weather·map | 변동 없는 데이터셋 304 비율 |
| 5 | A3 (P0-A 사전 구성) + 배포 hook | weather·Manager | 첫 페이지 p95 ≤1s(설계 목표), 재시작 직후 첫 순회 |
| 6 | 경로 B lifespan warmup + `/ready` | weather·Manager | 배포 직후 첫 마커 묶음 vs 두 번째 묶음 차이 |
| 7 | A7·A8·A9 | map | current 실패율 16.7% → 목표 1% 미만 |
| 8 | L4~L8, B3·B6~B12 | weather | 개별 PR |

## 8. 확인하지 못한 것

- 운영(n150·odroid) 접속: SSH 인증이 거부되어 실측·설정(Map read timeout·page size 실효값,
  Manager override의 uvicorn worker 수)을 확인하지 못했다.
- current 한 번 순회의 실제 페이지 수, A1의 실제 효과(추정치).
- PG 버퍼 캐시와 수집기 간섭의 분리 측정.

## 9. 이행 현황 (2026-11-24)

기준: kor-travel-weather `origin/main@ca3c0a6`, kor-travel-map `origin/main@ac45ce883`. 각 PR의 상태는
2026-11-24에 `gh pr view`로 다시 확인했다(머지 commit은 앞 7자리). **§2.5·§3.2·§5의 제품 코드 항목은
모두 머지됐다.** 다만 §7의 수용 기준은 전부 운영(n150·odroid) 배포 뒤 측정이 필요하고, 이 표의 어느 PR도
운영에서 재지 않았다. PR 본문의 수치는 모두 로컬 측정이다.

### 9.1 항목별

| ID | PR | 상태 | 한 일 | 남은 일 |
| --- | --- | --- | --- | --- |
| A1 | weather #290 (다른 작업자) | 머지 `677c39a` | current cursor 페이지 상태 판정을 snapshot·manifest digest당 한 번(승인 수명 60초 안, 남은 수명 20초 이상이면 재사용, head·generation·plan 삭제 epoch는 즉시 반영), current 페이지 상한 128→512, artifact 64개 묶음 읽기 | 배포 뒤 수용(`W-EXPORT-CURSOR-REUSE-20261124`). 판정 만료 순간 동시 재계산은 합치지 않음 |
| A2 | weather #299, map #1550 | 둘 다 머지 `2dfade8`·`8a6e3c5` | 약한 내용 ETag(`content-v1`: manifest digest+페이지+상태), current·locations 첫 페이지는 본문 읽기 전 304, 200·304 모두 `Cache-Control: no-store, private`. Map은 계약 시험만 추가(소비 코드 변경 없음) | daily는 값을 읽은 뒤 비교(요약 revision token 필요), forecasts는 그대로(Map이 INM 안 보냄), current 304도 freeze 비용은 남음. 배포 직후 옛 ETag는 데이터셋마다 한 번 200. 304 비율 운영 측정 |
| A3 | weather #301 | 머지 `5f6bf6f` | W2 current(limit 2000) 표준 페이지 사전 구성: migration 0044, `weather_export_prebuild_job`(KST :23·:38, priority 0), 요청은 입력 proof·시간 경계만 검산, 끄기 `KOR_TRAVEL_WEATHER_SERVICE_EXPORT_PREBUILD_ENABLED`, `deploy/deploy.sh` hook(실패는 경고). 설명은 `docs/export-prebuilt-pages.md` | **W5(daily, days 14) 프로필 미착수** — 요약 watermark 등 가산 opt-in DTO와 Map 소비자 검증이 함께 필요. 운영 적중률(`x-export-prebuilt`) 측정. 운영 배포 경로(Manager)의 hook은 Manager 저장소 일(없어도 다음 :23·:38 job이 만든다) |
| A4 | weather #305 | 머지 `ca3c0a6` | current freeze를 읽기 전용 선택 RR + 짧은 발급 TX(`lock_timeout` 1초)로 분리, 재사용은 선택 RR 기준 최신 handle | freeze 예산 초과 시 `_previous_success`를 stale로 주는 경로는 **하지 않음**(503 계약·stale 의미 차이, 소유자 결정 필요 — `docs/reports/2026-11-24-export-freeze-split.md`). 선택 RR 자체 길이와 daily·forecast·locations freeze는 그대로 |
| A5 | weather #295 | 머지 `ec70c30` | INM 없는 예보 첫 페이지의 페이지 캐시 강제 우회(`force`) 제거, 기존 캐시 hit 검사로 재사용(첫 페이지 즉시 반영 계약 유지) | hit 검사의 fresh freeze 비용은 남음 |
| A6 | weather #295 | 머지 `ec70c30` | `http.disconnect` 감시, 단계·SQL 문장 앞 checkpoint, 실행 중 SQL은 psycopg `cancel_safe()`, 응답 499·`cancelled` 지표. 같은 키 대기자가 있으면 leader는 45초 유예 | 운영 proxy가 연결 끊김을 upstream에 전하는지 미확인. 진행 중 FETCH는 취소되지 않음(문장 5초 상한) |
| A7 | map #1542 (다른 작업자) | 머지 `e2e7e66` | current 상한 480→**600초**(임시), 상류 미준비(503·무응답·상한 안 순회 불가)는 step 재시도 없이 건너뛰고 세대 유지, 같은 cursor 이어 읽기 | Weather 수정(A1~A4)이 배포되면 Map이 상한을 480초로 되돌린다(map `weather_state.py` 주석). 제안한 900초는 채택되지 않음 |
| A8 | map #1542 | 머지 `e2e7e66` | weather 상태 job 일곱이 Valkey 공용 자리 `weather.export_read`(동시성 1)로 줄 섬, 발화 분리(W5 :41→:05, 단기 :30→:50, 중기 :40→:50) | read timeout은 바꾸지 않음(코드 기본 60초, map 주석상 운영 300초). 운영 실효값 확인 |
| A9 | map #1542 | 머지 `e2e7e66` | 200+stale은 교체하되 run tag `kor_travel_map.upstream_state=stale`·WARNING, 서비스 세대 나이가 `stale_alert_after`를 넘으면 재시도 없는 실패(소유자 결정 "나이 기준 혼합") | n150 회전 뒤 확인(#1542 본문 "회전 뒤 확인" 목록) |
| L1·L2·L3 | weather #292 | 머지 `a0b5ef5` | detach 보존 검사를 lock 전으로(lock 뒤 300ms 재확인), drop 검사를 참조 표 lock 전으로, 야간 DDL lock 대기 400ms·차단자가 autovacuum뿐일 때만 긴 대기·첫 시도 전 quiet 대기 | 야간 유지보수 중 지도 읽기 p95·lock 대기 운영 측정. 실제 autovacuum 경로는 대역 세션으로만 시험. 연기(`partitions_deferred`)가 늘 수 있음 |
| L4 | weather #297 | 머지 `6b4e655` | 포인터·원천 정리를 기본키 keyset 1,000행 batch·batch별 commit·`statement_timeout` 10초·`lock_timeout` 1초로, 원천은 실행당 1,800초 예산·이어 읽기 | 운영 batch 시간·원천 한 바퀴 길이 미측정 |
| L5 | weather #297 | 머지 `6b4e655` | 수집 writer transaction에 문장 **300초**(권고 30초는 정상 chunk를 거짓 실패시킴)·유휴 60초 | role 수준 `idle_in_transaction_session_timeout`은 API·Dagster가 같은 role이라 하지 않음(`docs/write-path-bounds.md`) |
| L6 | weather #297 | 머지 `6b4e655` | 재수집 판정 조회에서 `defer(SourceRecordRow.payload)` | 없음 |
| L7 | weather #297 | 머지 `6b4e655` | `alembic/env.py` 연결 옵션 `lock_timeout=5s` | 재시도 헬퍼 표준화는 하지 않음(근거가 서로 달라 문서 규칙만) |
| L8 | weather #305 | 머지 `ca3c0a6` | A4와 같음 | A4와 같음 |
| §3.2 공개 읽기 `lock_timeout` | weather #300 | 머지 `8b57b46` | 공개 읽기 `lock_timeout = min(2초, 남은 마감)`, 55P03 → 503 `LOCK_TIMEOUT` | 없음 |
| B1·B2·B4·B5 | weather #294 | 머지 `2e69dd1` | BFF 세션 확인 GET/HEAD 10초 캐시·single-flight, matcher에서 `maplibre/` 제외, 공개 경로 DB 단절 503 `DB_UNAVAILABLE`, GET 시도별 상한·502/503/504 재시도. 계약 `docs/weather-admin-map-call-resilience.md` | `auth_limiter`(2)는 바꾸지 않음. `/v1/admin/*`의 DB 단절 분류는 범위 밖. 운영 503 감소 측정 |
| B3·B9·B10·B11 | weather #300 | 머지 `8b57b46` | 공개 handler의 DTO·직렬화·gzip을 worker thread로, 요청당 연결·transaction 하나와 마감 bind 1회, `/resolve` SQL 한 문장, 마커 자리 대기 3초 → 503 `MARKER_BUSY`. 계약 `docs/weather-public-read-path.md` | 요청 묶음 transaction의 lock 보유가 L3 연기에 주는 영향과 효과는 운영 미측정 |
| B6·B7·B8·B12 | weather #304 | 머지 `c037fbc` | 마커 시간 기반 재시도, `initialCamera`로 load 전 마커 요청, `view=compact` 마커(기본 응답·OpenAPI 200 불변), 카탈로그 병렬 수신·TanStack 캐시·ETag/304. 계약 `docs/weather-admin-map-calls.md` | compact 모양은 `x-compact-schema`로만 문서화. 카탈로그 304는 DB 비용을 줄이지 않음 |
| §4.3 경로 B warmup·`/ready` | weather #298 | 머지 `72dd9e9` | API lifespan 읽기 전용 warmup(30초 예산), `GET /ready`, `deploy.sh`의 `/ready` 확인(초과는 경고), compose web healthcheck. 설명 `docs/weather-api-prewarm.md` | Manager verify의 `/ready`, Manager 선언의 web healthcheck, HAProxy 트래픽 게이트는 Manager 저장소 일. odroid 효과 측정. `pg_prewarm`은 선택 사항이라 하지 않음 |

### 9.2 §7 순서별

| 순서 | 묶음 | PR | 코드 | 수용 기준 측정 |
| --- | --- | --- | --- | --- |
| 1 | A1·A5·A6 | #290·#295 | 머지 | 미측정(odroid current 순회·Map 상한 초과 건수) |
| 2 | L1·L2·L3 | #292 | 머지 | 미측정 |
| 3 | B1·B2·B5·B4 | #294 | 머지 | 미측정 |
| 4 | A2 + Map 계약 시험 | #299·map #1550 | 머지 | 미측정(304 비율) |
| 5 | A3 + 배포 hook | #301 | W2만 머지, W5 남음 | 미측정 |
| 6 | 경로 B warmup + `/ready` | #298 | 머지(Manager 쪽 남음) | 미측정 |
| 7 | A7·A8·A9 | map #1542 | 머지 | 미측정(current 실패율) |
| 8 | L4~L8, B3·B6~B12 | #297·#305·#300·#304 | 머지 | 미측정 |

§6에 적은 다른 작업자 PR(#282·#283·#284·#285·#286)도 모두 머지됐다. 그 뒤 들어온 관련 PR은
#288(단기예보 정리 연기 경고화), #289(export publisher 정리 job 분리), #302(Dagster 우선순위)이다.

### 9.3 남은 일

1. 운영 배포(Codex 몫)와 §7 수용 기준 측정. 특히 Map current run 시간·상한 초과 건수, `x-export-status-proof`·
   `x-export-prebuilt` 분포, 304 비율, 499 건수, 야간 유지보수 중 지도 읽기 p95.
2. A3 W5 프로필: 가산 opt-in DTO(OpenAPI·TS 동반)와 Map 소비자 검증을 함께 하는 후속 PR.
3. A4 stale fallback: Map 계약과 함께 소유자가 정한다.
4. Manager: `/ready` verify, web healthcheck, HAProxy 게이트, 운영 배포 경로의 사전 구성 hook.
5. Map: Weather 배포 확인 뒤 current 상한 600→480초 복귀, 운영 read timeout 실효값 확인.
