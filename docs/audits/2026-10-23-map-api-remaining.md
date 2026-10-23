# Weather W1~W5 남은 API 작업 감사

- 조사 시각: 2026-10-23 04:30 UTC 전후(13:30 KST).
- WT: `/mnt/f/dev/kor-travel-weather-map-perf`.
- 코드 main 기준: `91801d4593fad3b95aafd7456a5d94b56dc23130`(PR136 머지).
- 검토 문서 HEAD: `a46b3923db7d2068b5c3052295dddc344c3c71c5`(PR137).
- 범위: 소스/정본 문서/기존 로컬 검증 증거/GitHub PR 읽기. 제품·공유DB·운영·수집 설정 변경0, 신규 테스트0. `/tmp` 감사 문서만 작성했다.
- 운영 서버에 접속하지 않았다. 아래 API 응답은 현재 소스와 격리 실제 HTTP 증거이며, 공개 운영 주소의 현재 응답을 새로 실측했다는 뜻이 아니다.

## 1. 최종 문서 리뷰 상태

Weather PR137 HEAD a46b3923db7d2068b5c3052295dddc344c3c71c5: PASS. 직전 P2의 schema 표현을 '운영 DB schema 반영·운영 배포는 아직 없다'로 수정했다. migration0023 구현·머지와 운영 적용을 구분한다. 새로운 P0/P1/P2는 없다.

병행 요청 Transport 문서 PR111 HEAD b7aa0ea5a2ca1e603bb377fe84c919b996792918: PASS. 실제 정상 재관측이면 replay0도 확인 시각이 전진함을 명시했고, API 자체 RR session에서 head와 read model을 읽도록 고쳤다. 제품 변경0이다.

## 2. 실제 완료한 것은 계약과 receipt 기반

GitHub 읽기 확인:

| PR | 상태/정확한 head | 의미 |
|---|---|---|
| [132](https://github.com/digitie/kor-travel-weather/pull/132) | MERGED | W1~W5 정본 설계·백로그 문서 |
| [133](https://github.com/digitie/kor-travel-weather/pull/133) | MERGED | W5 실제 KMA 초단기실황 관측 포함 선택 문서화 |
| [134](https://github.com/digitie/kor-travel-weather/pull/134) | MERGED | 프록시·canonical receipt·단계 의존 문서 보강 |
| [135](https://github.com/digitie/kor-travel-weather/pull/135) | MERGED, head0b820b8f35707b0743e2ad8464ebf971bae1b7fe, merge9d75b64995a75bde0cc96e4417cbe0f983f67e28, 03:06:01Z | DTO/인증/OpenAPI/TS/503와 브라우저 service proxy 차단 |
| [136](https://github.com/digitie/kor-travel-weather/pull/136) | MERGED, head9bd5f213973910dbe5d2ba3838722569f4a4c854, merge91801d4593fad3b95aafd7456a5d94b56dc23130, 04:10:54Z | canonical accepted receipt·불변 bounded chunk의 opt-in 기반 |
| [137](https://github.com/digitie/kor-travel-weather/pull/137) | 조사 뒤 MERGED, head a46b3923db7d2068b5c3052295dddc344c3c71c5 / merge ccca1e51903ba5297f25440729d387ae4cb68fe7 | 수용 근거와 3A/3B 계획 문서. 제품 기능 추가 없음 |

이후 PR 상태는 바뀔 수 있으므로 이 표를 고정 조사 시점으로 해석한다.

증거:

- PR135: 전체 Python738/frontend243, CI·독립2리뷰, 격리 실제 API3범주/UI11 검증 기록.
- PR136: 전체 Python756 PASS1150초는 초기6d764da에서 시작한 baseline이고, 최종9bd5f21은 집중24 PASS29.29초·정확 HEAD CI·독립2리뷰로 후속 수정 검증했다. 756 전체를 최종 HEAD에서 다시 실행했다고 쓰면 안 된다.
- `/tmp/weather-receipts-full-python.log`, `/tmp/weather-receipts-review-focused.log`, `/tmp/weather-receipts-full-ui.log`와 수용 문서가 일치한다.
- `/tmp/weather136-live/report.json`: source9bd5f21, operational_deploy=false, API3범주, UI11, upstream service0, browser_errors0.
- `/tmp/weather136-live/ui-report.json`: 실제 로그인·기존 위치 UI·390/1440 폭·6 HTTP method service proxy 차단·기존 BFF. route_mocks=false인 격리 production build 실제 HTTP다.
- 위 API 검증의 성공은 '인증 후503, 무인증401'이라는 현재 계약을 검증한 것이다. 데이터 제공 성공200/304·실제 성공 head·운영 배포 수용을 뜻하지 않는다.

코드 근거:

- `packages/kor-travel-weather-api/src/kortravelweather_api/routers/service_exports.py:97-115`: `_unavailable()`은 EXPORT_NOT_READY, HTTP503, source_status=failed, item_count0, source_collected_at=null, no-store/private를 반환한다. 빈 성공200이 아니다.
- 같은 파일119-137: locations/current/status 세 route 모두 무조건 이 helper를 호출한다. repository/provider를 읽지 않는다.
- `packages/kor-travel-weather-api/tests/test_service_exports.py`: DB/provider 접근 시 실패하는 test double로 '읽기0'과 인증/503 계약을 검증한다.
- `src/kortravelweather/export_receipts.py`, `export_artifacts.py`, repository opt-in hook, migration0023은 존재한다. 그러나 Dagster collector/CLI에는 `ExportCapture`/`begin_export_attempt` 호출이 없다(검색 결과는 tests와 repository뿐).
- attempt schema에 sealed 상태 문자열은 있어도 seal/barrier/terminal evidence/head 승격 구현은 없다. enum/테이블 존재를 실제 sealed 기능으로 세면 안 된다.

## 3. W1~W5 요구·현황·구체 누락

| 항목 | 요구 | 실제 구현/응답 | 머지/검증/운영 상태 | 남은 핵심 |
|---|---|---|---|---|
| W1 locations | 활성 위치 id/name/좌표/법정동·시도·시군구, 안정 pagination, 메타 변경·비활성 tombstone | DTO+route만 존재, 인증 후503 | PR135 계약/인증 검증. 성공 데이터 query·운영 활성화 없음 | 성공 catalog generation/dirty·주소 revision·null 좌표, frozen snapshot·pagination·실제200 |
| W2 current | 위치/provider/metric별 observed·nowcast 최신값, 실패 attempt 제외, canonical identity/원래 clock 보존 | DTO+route만 존재, 인증 후503 | PR135 계약 + PR136 공통 receipt 기반. 실제 성공 head 없음 | collector opt-in 계획·성공 head/projection·winner·alias/replay0/CAS0, 일반예보/특보 대표 제외·실제200 |
| W3 forecasts | style/provider 필터, location/provider/style 완전 group, aligned target/metric arrays, snapshot/checkpoint·gzip/ETag·미래 필터 | service route/DTO/실제 query 없음 | 설계만. merged API source에 `/forecasts` handler 없음 | 성공 group materialization, dataset winner/단위·정규화 revision, frozen cursor와 delta/tombstone, 응답 시점 target>=now·epoch/304 |
| W4 status | provider/style/scope별 실제 성공·실패·다음 schedule, timezone/정지 구분·worst-status | DTO+route만 존재, 인증 후503 | PR135 계약만. status projection/운영 활성화 없음 | last_success/last_attempt 분리, scope health·stale threshold, bounded Dagster schedule cache/projection, stopped/unset next=null·실제200 |
| W5 daily summaries | 14일 기본/31일 최대, KST3slot, 검증 관측 사전집계·coverage/status·retention/backfill | service route/DTO/표/worker/dirty 상태 없음 | PR133 eligibility 선택 문서화만. 구현 미착수 | 성공 관측 projection 뒤 summary schema·dirty CAS·bounded worker/backfill·조회/API·실측/운영 |

기존 `/v1/weather/*` 또는 admin 지도 UI가 데이터를 제공하더라도 W1~W5 service API 완료의 근거가 아니다. 그 경로는 실패 run의 mutable current와 공개 성공 snapshot의 구분을 보장하도록 아직 연결되지 않았다.

W1/W2의 약1450/32000, W3 약1.8M은 설계 규모이며 이번 감사가 운영DB에서 새로 센 수치가 아니다.

## 4. 우선순위와 의존 관계

### P1: 공개 안전성의 선행 조건

1. **3A 계획·seal·terminal 검증(가장 작은 다음 제품 PR)**
   - expected source roster와 deterministic chunk/segment 계획을 receipt와 독립적으로 고정한다.
   - exact set/digest·명시 empty/retention accepted0·alias/replay0 완결성을 검증한다.
   - 안정 provider/dataset/style/grid scope, owner epoch/current attempt fencing, immutable plan/seal.
   - native exact SyncRun success; Dagster는 별도 reconciliation에서 exact run의 SUCCESS 확인. 기존 bool `terminal_worker().is_finished`를 성공 판정에 재사용하지 않는다.
   - missing/timeout은 pending, failed/canceled/partial은 reject. 수집 job이 자기 terminal을 기다리지 않음.
   - 첫 PR은 schema/lifecycle/helper와 synthetic 회귀·내부 bounded entry만; collector OFF·공개 head 없음 유지.

2. **3B 성공 head CAS·실패값 분리**
   - 성공 head를 기반으로 검증된 immutable 변경 group만 연결한다. 실패 A current 뒤 성공 B insert0/CAS0도 검증 대상으로 포함한다.
   - late success/동시 CAS/rebase/superseded 정책, scope별 clock/health/change watermark 원자 commit.
   - 예보1.8M 값 전체 복사 금지. 완전 group 교체/독립 delta/retire를 명시적으로 구분한다.
   - collector 통합 범위를 작은 provider/dataset부터 소유하고 `publish_and_finish`의 final source-only/empty 처리까지 검증한다.

3. **성공 projection의 frozen snapshot + W1/W2/W4 실제 공개**
   - W1 catalog/좌표·주소/비활성 generation, W2 성공 point winner, W4 작은 상태 projection을 독립해서 구현한다.
   - 기존 service 인증·브라우저 차단 유지. 초기 필수 scope 없음503, 실패 후 이전 성공+stale; scope worst-status/min source clock.
   - snapshot/filter/schema/limit/byte-cap revision, opaque handle·cursor, 실제 DTO/OpenAPI/TS 동시 갱신.

### P2: 후속 기능·완주 조건

4. **W3 완전 group·checkpoint·future/ETag/압축**
   - target 합집합·metric 배열 alignment, dataset/style winner·단위 revision, group atomic page cap.
   - same known_at correction/late commit/tombstone을 잃지 않는 durable checkpoint. since timestamp 단독을 완전 증분이라고 하지 않는다.
   - 응답 clock에서 과거 target 제거, 다음 만료 경계/health epoch 뒤304 금지. 페이지 snapshot TTL과 checkpoint 보존 분리.

5. **W5 사전집계**
   - W2용 current의 최신1점만으로31일 관측을 재구축할 수 없다. 성공 관측 history/projection을 별도로 보존·검증해야 한다.
   - 적격 OBSERVED + 실제 KMA `python-kma-api/kma_ultra_short_nowcast` NOWCAST만; external NOWCAST/일반 forecast 제외.
   - T1H/REH/WSD/RN1/PTY typed unit·obsr_value evidence·range/missing 검증. KMA SKY는 실황에 없으므로 다른 예보에서 채우지 않는다.
   - KST05~11/11~17/17~23 반열린 slot,23~05 제외. RN1 interval/reset/중복·slot crossing 불확실은 null+coverage이며 임의 시간비례 배분 금지.
   - summary_status와 temporal partial 분리, metric별 count·대표 provider·정책 revision, dirty CAS 재계산·31일+여유 요약 retention·bounded backfill.
   - 기존 원본 history 보존과 단기예보 과거6시간 정책을 요약 retention과 혼동하지 않는다.

6. **W-MAP-05 소비자·운영 수용**
   - 제품 API 실제 응답·OpenAPI·PR을 Map 담당에게 배포 전 전달하고 auth/health/304/checkpoint/future 계약 수용.
   - 정확 source HEAD 기능 CI·2독립 리뷰·WSL/정본 Docker·격리HTTP/live API/UI·rollback.
   - Manager/NVMe 정본으로 schema/API/CS/sensor artifact 공유경로·권한·release 일치 확인. 이 문서 감사는 배포 허가/실행이 아니다.
   - daemon은 최신 사용자 지시의 RUNNING 상태를 보존하며 필요한 경우 해당 Weather instigator만 승인된 배포 절차로 일시 보류·복원한다.

의존 흐름:

`계약 PR135(완료) -> receipt 기반 PR136(완료) -> 3A -> 3B/collector 통합 -> 비용 gate/frozen snapshot -> W1/W2/W4 실제 값 + W3 -> Map 수용/배포`

`W5 선택 PR133(문서완료) + 성공 관측 projection -> W5 집계/dirty/retention/backfill/API -> Map 수용/배포`

설계/DTO/집계 순수함수 준비는 선행 병행할 수 있지만, 성공 증거 없는 mutable current를 공개하거나 W5 값을 운영 집계하는 것은 선행 gate를 건너뛰는 것이다.

## 5. CPU·메모리 비용 gate는 별도 미완 작업

현재 receipt는 collector opt-in OFF다. 따라서 PR136으로 운영 수집 CPU/RAM이 개선됐다는 주장은 성립하지 않는다. chunk당8MiB는 전체 worker RSS 상한이 아니며 JSON bytearray/bytes/DTO/evidence의 일시 복사와 PG 비용을 포함해 측정해야 한다.

필수 비교:

- 성공 증거를 가진 indexed current-query/작은 manifest baseline vs COW 후보.
- 변경0/소수group/전체scope 갱신, 동일 normalized 입력·cold/warm·호스트 부하, 각각 최소3쌍.
- collector+serializer+publisher+PG 합산 CPU core-seconds/wall/peak RSS·cgroup/WAL/temp bytes/I/O·buffers/export-ready lag/backlog/API p95.
- 변경0 새 대형artifact0, 전체1.8M scalar 세대별 복사0, count/byte 상한 위반0, 실패partial 공개0.
- 현재 설계 gate는 baseline 대비 CPU/WAL/peakRAM/API p95 10% 초과 악화 시 채택·확대 중단이다. actual baseline을 고정하고서 후보를 평가해야 한다.
- backlog가 한 poll 주기/정상 stale budget을 넘으면 worker를 무조건 늘리지 말고 staging/serialization/manifest 경로를 보강한다.
- artifact는 NVMe에만 두고 CS producer/API reader/reconciler가 같은 불변 저장소를 보게 한다. 임시파일도 같은 NVMe filesystem이어야 atomic rename이 유지된다.
- pending attempts/GC 전 artifact의 전체 디스크 예산·동시 생성 수를 제한한다. page lease·checkpoint·head·pending validation이 참조 중인 bytes는 삭제하지 않는다.
- full-copy 없는 manifest라 해도 HTTP마다 무한 delta chain resolve가 생기면 안 된다. 공개 전 bounded checkpoint/index와 read cost 실측이 필요하다.

PR118 등의 과거 합성 fixture RSS 개선, PR124 지도 상세 SQL 개선, PR130 KMA 병행 수집의 과거 결과를 새 export COW 경로의 비용 gate 통과로 전용하면 안 된다. 이번 감사에서는 성능 실험을 하지 않았다.

## 6. 복구/실패 경계에서 반드시 남는 일

- 300source 중32chunk, 동일count 다른scope, segment 중복·누락, 정상0/오류 구분.
- owner 탈취·느린 reconciliation·취소·Dagster ID 누락/native 강등 차단.
- commit 응답유실·파일누락·손상·FIFO와 잠금실패표시가 raw 성공을 취소하지 않음.
- **terminal 이후 누락 파일은 현재 ingest fence 때문에 same-run 재생이 안 된다.** 정확 bytes가 따로 없으면 새 수집/새 attempt이며 이전 성공 head 유지. 자동 mutable current 복원은 금지.
- file 검증→CAS 사이 GC race, head commit 응답유실 idempotency, frozen reader 도중 source 수정·삭제·예보만료.
- state failure/stale threshold/TTL 변경이304를 통해 숨겨지지 않음.
- W5 원천 중복/교정/늦은 도착·강수 interval/미확정 coverage·초도 not_ready/실패/정상무관측 구분.

## 7. 감사 중 발견한 문서 후속 정리

이 절은 현재 PR137 변경에 새로 생긴 제품 결함이 아니라, 이전 문서가 누적되며 남은 운영 지침/작업원장 정리다. 제품 작업과 별개로 작게 정리할 수 있다.

1. `docs/tasks.md:5`는 'daemon OFF는 현재 사용자 정책'이라고 적지만 최신 사용자 지시는 Geo/Weather/Transport daemon RUNNING이다. 날짜가 붙은 과거 OFF 관측은 보존하고 현재 정책 문장만 수정해야 한다. `docs/resume.md`의 과거 회수 센서 절 OFF 명령도 현재 지침처럼 실행하지 않도록 한정할 필요가 있다.
2. `docs/tasks.md:8-11`의 'W-MAP-03 외 모두 미착수'는 PR135/136 머지 사실과 충돌한다. '실제 데이터 공개는 미완료, 계약/receipt 기반은 완료'로 정산할 수 있다.
3. `docs/tasks.md:15`의 W-MAP-01은 본문에 구현·검증 완료라고 적혀 있으나 checkbox가 미완료다. 계약 범위만 완료 항목으로 구분하고 실제 W1/W2/W4 공개 task는 W-MAP-02에 남기는 것이 정확하다.
4. 이전 dated journal/resume의 '단계1 미착수/머지대기'는 당시 사실로 보존하되 최신 절/현재 다음 작업과 충돌하지 않게 이력임을 표시한다. 과거 전체 문서를 일괄 rewrite하지 않는다.

## 가장 작은 다음 실행 제안

문서의 현재 정책/작업원장 위3항목을 별도 작은 doc 정산으로 처리할 수 있다. 제품은 **3A 계획·seal·terminal evidence** 한 PR부터 시작한다. first scope를 단일 안정 source로 제한하면 schema/검증 범위를 더 줄일 수 있으며 복수 source는 명시 reject해야 한다. 이때 반환은 NOT_READY/ELIGIBLE/REJECTED만 제공하고 public head/API200/collector 활성화를 섞지 않는다. 후속3B와 비용 gate·frozen 공개·W5·Map 운영 수용은 별도로 남긴다.


## 조사 후 문서 정산

PR137은 mainccca1e5로 머지됐다. 이 감사 문서화에서 tasks의 현재 daemon 정책을 최신 사용자 지시RUNNING으로 바로잡고, 실제 데이터 공개는 미완이지만 계약/receipt 기반은 머지됐음을 정산했다. W-MAP-01 체크는 계약만 완료임을 뜻하며 W1/W2/W4 데이터 공개나 W-MAP-02 완료를 뜻하지 않는다. dated 과거 OFF/미착수 이력은 보존한다. 운영을 바꾸거나 새 성능 실측을 했다는 의미가 아니다.
