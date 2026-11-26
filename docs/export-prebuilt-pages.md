# Map 표준 프로필 export 페이지 사전 구성 (호출 검토 A3)

상태: 구현(W2 current). 근거는 `docs/reports/2026-11-24-map-call-performance-review.md` §2.5 A3·§4.3·§7-5와
설계 `docs/design/weather-performance-use-cases-20261109.md` §P0-A·§6이다. W5는 아래 "하지 않은 것"의
이유로 후속이다.

## 무엇이 바뀌었나

Map `weather_state_current`(매시 :25·:40 KST)는 `GET /v1/service/exports/current?limit=2000`을 첫
페이지부터 끝까지 읽는다. 첫 페이지마다 요청 경로에서 freeze(catalog·승인 선택·manifest·handle 발급)와
artifact 본문 조립을 했고, 결과 캐시는 프로세스 메모리 60초라 다음 순회·API 재시작마다 처음부터
다시 만들었다.

- **builder** `kortravelweather.export_prebuilt.build_current_profile`: 요청 경로와 같은 reader 설정으로
  freeze를 한 번 하고 그 manifest의 **모든 페이지 본문(불변 data payload)**을 DB에 저장한다
  (`weather_export_prebuilt_builds`·`weather_export_prebuilt_pages`, migration `0044`). 입력이 마지막
  build와 같으면 아무 것도 쓰지 않는다(`unchanged`). 승인 선택이 아직 없거나 freeze가 이전 승인
  fallback을 써야 하는 상태면 만들지 않는다(`not_ready`).
- **실행 표면**
  - Dagster `weather_export_prebuild_job`(schedule `weather_export_prebuild`, `23,38 * * * *` KST,
    in-process, run 상한 900초, 실패 재시도 1, 공용 queue `dagster/priority` 10 — 아래 "실행 시각과
    승격 사이의 창"). publisher sensor에 넣지 않았다. Map W2 2분 전에만 돈다. export·사전 구성이 켜져 있고 publisher sensor가 RUNNING일 때만 run을 만든다.
  - 배포 hook: `deploy/deploy.sh`가 `/ready` 확인 뒤 `docker compose exec -T api timeout 280 python -m
    kortravelweather.export_prebuilt --budget-seconds 240`을 한 번 돌린다. 호스트의 `timeout 300`은
    docker exec client만 끝내고 컨테이너 안 프로세스는 남기므로, 컨테이너 안 `timeout 280`이 builder
    프로세스 자체를 끊고 builder 예산(페이지 사이에서 확인)은 그보다 짧은 240초로 준다. 끊겨도 저장은
    한 transaction이라 반쯤 쓴 build는 남지 않는다. 정상 결과는 Dagster job과 같은 집합
    (`built`·`unchanged`·`disabled`·`unsupported`·`not_ready`, `SUCCESS_OUTCOMES`)이고 그 밖의 결과·
    실패·시간 초과는 경고만 하며 배포는 성공이다.
- **요청 경로**(`current` 첫 페이지, `FrozenCurrentExports._page_guarded`)
  1. 짧은 RR 하나에서 **입력 proof**를 읽는다: 삭제 epoch 3개(page liveness), 기대 원천 승인 inventory
     전체 행 digest(SQL 집계), freeze와 같은 조회의 활성 catalog·주소 행 digest. builder가 manifest를
     만든 같은 RR의 proof와 다르면 기존 freeze로 간다. 시간 경계(`valid_until`)는 build를 버리는
     조건이 아니라 3의 builder 판정을 잇는 상한이다(아래 "시간 경계").
  2. handle: 같은 manifest digest의 유효 handle 중 freeze 재사용과 같은 조건(그 selection의 가장 최근
     발급)을 만족하는 것을 먼저 쓴다. build가 가리키는 handle이 만료된 뒤 앞 요청이 발급한 handle도
     여기서 이어 쓰므로, 요청마다 새 handle·scope ref를 만들지 않는다. 없으면 build의 handle을 쓴다.
     기존 재사용 handle과 같이 manifest 전체의 artifact 존재·크기를 확인하고, 유효 handle은 기존 조건부
     갱신(`_renew_reused`)을 한다.
  3. 상태: builder 판정(manifest와 같은 RR)의 남은 수명(승인 60초와 시간 경계 중 이른 것까지)이
     20초 이상이면 그 판정을 쓰고(`x-export-status-proof: prebuilt`), 아니면 기존 첫 페이지처럼 새로
     판정한다(`computed`). 새 판정의 승인 선택이 manifest와 다르면(scope별 위치 집합·미확인 여부)
     사전 구성을 버리고 freeze한다(`x-export-prebuilt: selection`).
  4. 새 handle 발급: 위 검산을 모두 통과했는데 남은 수명이 15분 미만이면(갱신 실패·총 수명 6시간
     상한·이미 만료) 같은 manifest로 **새 handle을 발급**한다. 버려질 handle을 남기지 않도록 검산 뒤에만
     발급한다. 만료 handle은 연장하지 않는다. 새 handle의 snapshot ref가 참조 세대를 GC에서 지킨다.
     발급하지 못하면(잠금 대기 등) `error`로 기존 freeze 경로가 결정한다. proof 판정 중 DB 오류(삭제
     epoch 조회가 올리는 `ExportArtifactError` 포함)도 같다.
  5. 본문: 같은 manifest digest·limit·시작 위치의 저장 본문을 sha256·byte·항목 수로 검산해 쓰고, 그
     페이지가 읽었던 artifact의 존재·크기는 기존 완료 페이지 재사용처럼 매번 확인한다. 저장 본문이
     손상됐으면 경고를 남기고 원본 artifact에서 다시 조립한다. `snapshot_id`·`next_cursor`·
     `generated_at`·상태는 요청에서 결합한다. cursor 페이지도 같은 manifest면 저장 본문을 쓴다.
- 응답 header(관측용, OpenAPI 계약 밖): `x-export-prebuilt`(`hit`·`none`·`changed`·`missing`·
  `artifacts`·`selection`·`error`·`off`, 판정이 없는 요청은 `-`), `x-export-prebuilt-pages`(이 요청이 쓴
  저장 본문 수). 응답 본문·ETag·304 의미는 바뀌지 않는다(같은 manifest면 같은 내용 ETag).
  (`expired`는 2026-11-25에 없앴다. 아래 "시간 경계".)
- 지표(API `/metrics`): `ktw_export_prebuilt_decisions_total{outcome}`(첫 페이지 판정, header와 같은
  값, 완료 페이지 재사용처럼 판정이 없는 요청은 세지 않는다)와 `ktw_export_prebuilt_pages_total{page}`
  (`first`·`cursor`, 저장 본문을 쓴 페이지 수). 적중률은
  `increase(ktw_export_prebuilt_decisions_total{outcome="hit"}[1h]) /
  increase(ktw_export_prebuilt_decisions_total[1h])`다. 같은 값을 INFO 로그
  `weather_export_prebuilt_decision outcome=… pages=… status_proof=…`로도 남긴다(로그 설정이 INFO를
  내보낼 때만 보인다).
- 끄기: `KOR_TRAVEL_WEATHER_SERVICE_EXPORT_PREBUILD_ENABLED=false`면 builder는 `disabled`로 끝나고 API는
  기존 freeze 경로만 쓴다. compose는 이 값을 `.env`에서 api와 dagster-code-server 두 서비스에 같은 값
  (기본 `true`)으로 넘긴다(`tests/test_deployment_contract.py`가 확인한다).

## proof가 덮는 것과 덮지 않는 것

manifest = f(활성 catalog 행, 주소 행, 승인 inventory 행, 세대·plan, 시각)이다.

- inventory는 승격 TX(`record_approved_locations`, 공개 clock과 같은 TX)에서만 upsert되고 행을 지우는
  경로가 없다. 운영 rollout용 `bootstrap_approved_locations`도 행 내용을 바꾸므로 digest가 잡는다.
- 세대·plan은 불변이고 삭제는 page liveness epoch가 센다.
- 시간에 따른 만료·신선도 경계는 builder 승인 선택의 `health_recheck_at`(가장 이른 경계)을
  `valid_until`로 둔다(보수적으로 이르다). 아래 "시간 경계"처럼 builder 판정을 잇는 상한으로만 쓴다.
- 실행·시도 상태(수집 실패 등)는 manifest의 `unconfirmed_sources`와 상태 판정에 들어간다. 그래서
  builder 판정을 쓰지 않는 요청은 새 판정의 승인 선택이 manifest와 같은지 확인한다. builder 판정은
  소유자 결정의 최대 60초 상태 반영 지연 안에서만 쓴다. builder의 no-op 판정도 proof가 같을 때 같은
  승인 선택 비교를 해서, 실패·복구처럼 미확인 여부만 바뀐 경우 다음 실행에서 다시 만든다.
- handle 수명은 no-op 판정에 넣지 않는다. 만료되면 첫 요청이 같은 manifest로 한 번 발급하고 뒤 요청이
  그것을 이어 쓴다(위 요청 경로 2).

## 시간 경계(`valid_until`)

처음 구현은 시간 경계를 지난 build를 요청이 통째로 버렸고(`expired`), builder no-op도 경계 5분
전부터 다시 만들었다. 운영(odroid, 2026-11-25)에서는 이 때문에 사전 구성이 한 번도 쓰이지 않았다.

- current 기대 원천 7개(승인 inventory 3,315행)의 경계(`source_collected_at`·`data_revision_at` +
  신선도 상한·만료) 중 가장 이른 것은 판정 몇 초~몇 분 뒤다. 07:24:47Z 판정 build의 `valid_until`은 07:24:52Z로 저장 시각
  07:25:34Z보다 앞섰다(만들자마자 쓸 수 없었다). 07:49:13Z build는 08:00:00Z(초단기실황
  `data_revision_at` + 3시간, 373행)였다.
- 경계가 manifest에서 바꿀 수 있는 것은 승인 선택의 만료 위치(scope별 위치 집합)와 미확인 여부뿐이다.
  세대·행 내용은 proof가 덮는다. builder 판정을 잇지 않는 요청은 이미 새로 판정한 승인 선택(첫
  페이지라 공용 선택 캐시도 이 요청 시작 뒤에 읽힌 것만 쓴다)을 manifest와 비교하므로(`selection`),
  경계를 지났다는 이유만으로 build를 버릴 필요가 없다.

그래서 지금은 경계를 **builder 판정을 잇는 상한**으로만 쓴다. 요청은 `min(판정+60초, valid_until)`이
20초 넘게 남았을 때만 builder 판정을 잇고, 아니면 새로 판정해 승인 선택을 비교한다. builder no-op도
시간 경계를 보지 않고 proof·승인 선택 비교만 한다(경계가 선택을 실제로 바꾸면 비교가 다르다).

## 동시성·수명·GC

- 요청 경로는 builder를 부르지 않는다. 미준비·proof 불일치는 기존 경로(같은 export 슬롯 2개·같은
  키 단일 생성)로 간다. 동기 재구성이 늘어나지 않는다.
- builder는 시도마다 새 승인 선택을 manifest·proof와 같은 RR에서 읽는다(앞 시도의 공용 선택을 잇지
  않는다). 저장은 build 행과 모든 페이지를 한 transaction(문장 30초·lock 1초)에 넣는다.
- profile마다 판정 시각 순으로 최근 build 2개만 남긴다. 늦게 끝난 builder는 더 새 build를 지우지
  않으며, 어느 build를 고르든 요청이 proof를 다시 검산하므로 정확성은 build 선택과 무관하다.
- build는 snapshot handle을 FK(`ON DELETE CASCADE`)로 가리킨다. 기존 `prune_export_snapshots`가 만료
  6시간 뒤 handle을 지우면 그 build와 본문도 함께 지워진다.

## 큰 이력 표 키 조회 묶음(2026-11-25)

운영(odroid)에서 `weather_export_prebuild_job` 12회 중 2회(18:18·21:24 KST)가 `statement_timeout`으로
실패했다. PostgreSQL 로그의 취소 문장은 두 번 모두 `_active_scope_states`의 시도 PK 조회
(`weather_export_attempts.attempt_id = ANY(약 2,900개)`)였고, 상한은 freeze 선택 RR의 5초다.

- 원인: current 승인 선택은 기대 원천 7개의 scope 약 2,900개마다 시도(112만 행·844 MB)·세대(75만 행·
  470 MB)를 임의 UUID 배열 한 문장으로 찾는다. 키가 흩어져 있어 비용은 키 수만큼의 index leaf·heap
  page 임의 읽기다. 같은 조회가 데워진 cache에서는 0.2초, 차가운 cache에서는 2.9~3.6초였다(한가한
  13:04 UTC 실측: 시도 3.2초·세대 3.0초·plan 크기 예비 조회 3.6초, 선택 전체 18초). 실패한 두 시각은
  초단기실황·WeatherAPI 수집과 transport `highway_collection_job`이 겹쳐 5초를 넘었다. builder는 run마다
  새 프로세스라 plan 기억(`_parsed_plans`)도 늘 비어 있다.
- 변경: 이 조회들(owner·시도·공개 세대·run·승인 세대·plan 존재·plan 크기 예비 조회·캐시 선택 검산)을
  같은 RR 안에서 `KEY_LOOKUP_BATCH`(512)개씩 나눠 읽는다(`export_capacity.batched_rows`). 결과·RR
  일관성은 같고, 한 문장의 일은 키 수와 무관하게 묶인다(키 512개 ≈ 차가운 cache 0.6초, 실패 때 관측한
  최악 속도로도 1초 안팎). 합계 일은 같으므로 선택 전체 시간은 줄지 않는다. 문장 상한(5초)은 그대로다.
- 같은 조회는 API 첫 페이지 freeze에서도 돈다(17:40 KST에 세대 조회가 같은 이유로 취소됐다). 이 변경은
  두 경로에 같이 적용된다. 실패 대신 같은 총 시간으로 응답한다.

## 측정(로컬, 운영 아님)

Windows 11 + WSL `postgres:16`(tmpfs) 위에서 `benchmarks/export_current_pages.py` 운영 규모 fixture
(KMA 격자 2,925 scope·위치 3,434·point 27,472)로 limit 2000 순회(14페이지)를 새 앱으로 3회씩 쟀다.
같은 프로세스·같은 engine이라 2·3회차는 DB artifact 복호 캐시가 데워져 있다. 다른 작업이 같은 머신을
쓰고 있었다. odroid의 디스크·CPU 경합은 재현하지 않았으므로 운영 수치로 옮기지 않는다.

| 경로 | 첫 페이지 | cursor p50 | 순회 전체 | 첫 페이지 SQL |
| --- | --- | --- | --- | --- |
| 기존(`--prebuilt off`, freeze) | 2.63 / 1.98 / 2.09 s | 0.36 / 0.34 / 0.38 s | 7.74 / 6.96 / 7.30 s | 41 / 34 / 34 |
| 사전 구성 + builder 판정(60초 안) | 0.79 / 0.71 / 0.72 s | 0.20 / 0.17 / 0.18 s | 3.51 / 3.00 / 3.20 s | 16 |
| 사전 구성 + 새 상태 판정 | 1.99 / 1.63 / 1.46 s | 0.20 / 0.18 / 0.19 s | 4.74 / 4.23 / 4.41 s | 28~31 |

builder 한 번(freeze + 14페이지 조립 + 저장, 5.7MB)은 7.9초, 입력이 같을 때 CLI no-op은 0.64초였다.
새 상태 판정이 필요한 첫 페이지는 승인 선택 재조회가 여전히 대부분이다. builder는 Map 순회 2분 전에
돌기 때문에 운영에서 첫 페이지가 builder 판정(60초 안)을 쓰는 경우는 드물 것으로 본다(미측정).

## 하지 않은 것

- **W5(daily-summaries)**: 설계 §P0-A는 W5 불변 프로필에 summary progress watermark·coverage digest·
  날짜창·partial 경계(11·17·23시)를 key/proof에 넣고, 기존 페이지별 최신 집계 계약과 구분되는
  가산 opt-in 계약 revision(`summary_watermark`·`summary_as_of`·전체 count/hash)과 Map 소비자 검증을
  함께 바꾸라고 한다. 이는 공개 DTO 변경과 Map 저장소 작업이 필요해 이 변경에 넣지 않았다.
- **W1·W3·임의 limit/필터**: 기존 경로 그대로다(설계 P0-A 범위).
- **상태 판정의 cheap proof**: 실행·시도 상태(`weather_sync_runs`·`weather_export_attempts`)에는 변경
  번호가 없어, 60초가 지난 상태는 승인 선택을 다시 읽는다. hot 표에 trigger를 더하는 것은 설계가
  금지한 "모든 writer 경로가 반영된다는 전제 미입증" 사례라 하지 않았다.
- **적중률 관측**: 위 지표(`ktw_export_prebuilt_decisions_total`)로 본다. 시간 경계 문제는 위
  "시간 경계"에서 고쳤다. builder의 freeze·페이지 조립은 API export 동시성 제한 밖에서 돌아 :25 첫
  페이지와 DB를 다툴 수 있다(운영 미확인).
- **실행 시각과 승격 사이의 창**(2026-11-25 운영 관측, 2026-11-26 우선순위만 상향 — 아래 "결정과 적용"): 적중하려면 build가 Map 첫
  페이지 전에 저장돼 있고, 그 판정과 Map 첫 페이지 사이에 current 기대 원천(초단기실황·AirKorea·
  OpenWeatherMap·WeatherAPI·산림청·도로공사) 승격이 없어야 한다.
  - Dagster run 시작이 schedule보다 늦었다: :23 run은 03:23:55·04:24:18·05:30:17·06:23:52·07:24:45Z,
    :38 run은 03:38:45·04:39:05·05:40:22·06:38:49·07:49:10Z에 시작했다(run 자체는 41~63초). Map 첫
    페이지 응답은 :25:28~:26:03·:40:37~:41:03이었다. 07:24 build는 07:25:34Z에 저장돼 Map 첫 페이지
    (07:25:39Z 응답)와 거의 같았고, :38 run 두 번(05:40·07:49)은 Map 뒤에 끝났다.
  - 승격은 매시 거의 연속이다(07Z 예: 초단기실황 07:26~07:31, AirKorea 07:48~08:05 이후, 06Z
    OpenWeatherMap 06:36~06:43). :40 Map 순회는 OpenWeatherMap 승격 창과 겹치는 시간대가 있어 그때는
    `changed`가 정상이다.
  - 선택지(설계 결정 필요): (a) schedule을 앞당기거나(예: :21·:36) run 우선순위를 올려 시작 지연을
    줄인다. 앞당기면 그 사이 승격으로 `changed`가 늘 수 있다. (b) Map 순회 시각을 승격이 조용한
    창(예: :20~:25 사이 초단기실황 전)으로 맞춘다(Map 계약 변경). (c) 승격 뒤 일정 시간 조용하면
    builder를 돌리는 sensor(#289 판단과 run 수 비용을 다시 봐야 한다). (d) 바뀐 scope만 다시
    조립하는 부분 재사용(설계 변경이 크다).
  - 재배포(19:25 KST) 뒤 지표의 `hit` 1건(19:26:43)은 127.0.0.1의 수동 첫 페이지 측정이었다. Map 순회
    6번(:25:51~:25:53·:40:31~:40:48 UTC, 첫 페이지 + cursor 13페이지, limit 2000에 약 2.7만 항목)은
    모두 첫 페이지가 `changed`였고, 그 순회의 cursor는 새 freeze manifest를 따라가 사전 구성 본문이
    없다. 그래서 `ktw_export_prebuilt_pages_total{page="cursor"}`가 없는 것은 정상이다(적중 뒤 cursor
    본문 사용은 `test_prebuilt_walk_serves_the_same_pages_without_reading_the_selection`이 지표까지
    확인한다).
  - 24시간 측정(11-24 21:40 ~ 11-25 21:40 KST, Map 순회 49번, 기대 원천 승격 생성 시각 기준). 승격은
    매 분 있다(분당 승격 초 수 최소 :25~:26 106~109, 최대 :49~:55 357~392). Map 첫 페이지 직전 조용한
    간격은 p10 1초·p50 34초·p90 29분이고, build 최소 시간(판정 뒤 약 57초)보다 긴 경우는 18/49(37%)다.
    어떤 실행 시각을 골라도 이것이 상한이다. 사전 구성 run은 생성→시작 지연 45~3,308초(p50 71초),
    실행 22~69초였고 실제 run 기준으로 적중 가능한 순회는 3/20이었다.

    | 선택지 | 가정 | 예상 적중 | build 수/일 |
    | --- | --- | --- | --- |
    | 현재(:23·:38, 우선순위 0) | 관측 시작 지연 | 3/20 (15%) | 96 |
    | (a) :23·:38 유지 + 우선순위 상향 | 시작 지연 ≤30초 | 14/49 (29%) | 96 |
    | (a) :21·:36 + 우선순위 상향 | 시작 지연 ≤30초 | 12/49 (24%) | 96 |
    | (c) 승격 뒤 60초 조용하면 sensor | tick 30초·시작 15초 | 14/49 (29%) | 약 92 |
    | (c) 같은 sensor, 조용함 30초 | 같음 | 15/49 (31%) | 약 157 |

    앞당기면 사이 승격이 늘어 오히려 떨어진다. (a)의 우선순위 상향(시작 지연을 줄이고 고르게)이 (c)와
    같은 적중을 sensor·추가 run 없이 낸다. 37%를 넘으려면 (b) 또는 (d)가 필요하다.
  - **결정과 적용(2026-11-26)**: 소유자가 (a)를 골랐다 — cron `23,38`은 그대로 두고
    `weather_export_prebuild_job`의 `dagster/priority`를 0에서 10(공용 상한)으로 올렸다(공통 규칙의
    예외, [`dagster-boundary.md`](architecture/dagster-boundary.md) "공용 queue 우선순위").
    아래는 바꾸기 전에 잰 queue 대기 원인이다(odroid 공용 Dagster 1.13.24, 11-25 12:00 ~ 11-26
    08:00 KST, run 1,157개·사전 구성 run 34개, GraphQL·daemon 로그·instance 설정 읽기 전용).
    - 운영 instance는 이 저장소 `deploy/dagster.yaml`이 아니라 Manager가 관리하는 공용 설정이다:
      `max_concurrent_runs` 5, `dagster/code_location` 상한 weather 3·transport 3·geo 1,
      `run_queue.dequeue_interval_seconds` 30, `DefaultRunLauncher`. daemon은 `dagster/priority`
      순(같으면 생성 순)으로 dequeue한다(로그 "Priority sorting and checking tag concurrency limits").
    - 생성→시작은 queue 대기(생성→`RUN_DEQUEUED`) p50 84초·20~5,729초와 launch+run worker 기동
      (`RUN_DEQUEUED`→`RUN_START`) 22~33초(p50 27)의 합이다. 막는 것이 없어도 tick 간격 30초와
      daemon의 직렬 launch(1 run p50 18초) 때문에 생성→시작이 45~60초 걸린다 — 우선순위로 줄지 않는
      바닥이다. schedule tick은 늦지 않았다(run 생성 :23:01~:23:05).
    - 시작 지연이 120초를 넘은 16번은 모두 상한이 찬 동안 기다렸다. 생성 시점에 weather location
      3개가 긴 run(`kma_short_forecast_job` 1~2시간, slow 외부 sweep 30분, `weather_retention_job`
      2시간)으로 찬 것이 7번, instance 5개가 찬 것이 5번(2번 겹침)이고, 나머지 6번은 남은 자리를
      다른 run이 먼저 가져간 뒤 찼다. 상한이 풀릴 때마다 생성이 앞선 우선순위 0 run
      (`weather_short_forecast_retention_job`은 최대 1시간 넘게 대기)과 5·10 run(transport
      `highway`·`airport` 5분 주기 10, 특보 발표 관측 10, 매시 수집 5)이 먼저 나갔다. 지난 26시간
      daemon tick 2,205번 중 1,034번이 "Maximum is 5"였다.
    - 같은 이력으로 공용 coordinator를 재현한 시뮬레이션(실제 생성 시각·점유 시간·tag 상한, tick 30초):

      | 조건 | 생성→시작 p50 / p75 / p90 / 최대 | 120초 이하 | 예상 적중(위 49 순회) |
      | --- | --- | --- | --- |
      | 우선순위 0(재현, 실제 p50 109) | 86 / 402 / 614 / 5,742초 | 19/34 | 8.6/49 (18%) |
      | 우선순위 5 | 43 / 342 / 458 / 1,272초 | 21/34 | 9.5/49 (19%) |
      | **우선순위 10(적용)** | 43 / 189 / 314 / 579초 | 21/34 | 9.5/49 (19%) |
      | 10 + weather location 상한 4 | 42 / 131 / 215 / 579초 | 24/34 | 10.9/49 (22%) |
      | 10 + location 4 + instance 6 | 39 / 42 / 98 / 312초 | 33/34 | 14.4/49 (29%) |

      시뮬레이션은 launch 직렬 시간을 빼서 바닥을 실제보다 10~30초 짧게 잡는다(우선순위 0의 실제 p50은
      109초). 우선순위 10은 꼬리(한 시간 넘게 밀린 run)를 없애지만, 120초를 넘긴 대기의 대부분은
      상한이 찬 동안이라 값과 무관하게 남는다. 적중은 build 끝이 Map 첫 페이지보다 늦으면 0이므로 거의
      늘지 않는다(18%→19%). 다른 job의 대기는 시뮬레이션에서 바뀌지 않았다(특보 발표 관측 queue p90
      613초 그대로) — 사전 구성 run은 1분 안팎·시간당 2개다.
    - 이전 표의 29%(시작 지연 ≤30초 가정)에 닿으려면 공용 instance 용량이 필요하다: weather
      location 상한 3→4와 `max_concurrent_runs` 5→6(Manager 설정, 이 저장소 밖). 그때는 우선순위와
      무관하게 29%다. 용량을 늘리면 odroid 부하(측정 중 load average 21~27)와 KMA·외부 provider 동시
      호출도 함께 늘므로 Manager 쪽 결정이다(`W-EXPORT-PREBUILT-CAPACITY-20261126`).
    - 배포 뒤 볼 것: 사전 구성 run의 생성→시작(`creationTime`→`startTime`) 분포와 꼬리,
      `increase(ktw_export_prebuilt_decisions_total{outcome="hit"}[1d]) /
      increase(ktw_export_prebuilt_decisions_total[1d])`, 같은 기간 특보 발표 관측·transport 10 run의
      queue 대기.
