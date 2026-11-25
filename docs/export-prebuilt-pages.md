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
    in-process, run 상한 900초, 실패 재시도 1). publisher sensor에 넣지 않았다. Map W2 2분 전에만
    돈다. export·사전 구성이 켜져 있고 publisher sensor가 RUNNING일 때만 run을 만든다.
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
- **실행 시각과 승격 사이의 창**(2026-11-25 운영 관측, 고치지 않음): 적중하려면 build가 Map 첫
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
