# Dagster boundary

KMA는 **dataset마다 하나씩** asset/job/schedule 경계를 갖는다: `kma_ultra_short_nowcast_sync`
(초단기실황, hourly `0 * * * *`), `kma_ultra_short_forecast_sync`(초단기예보, hourly
`0 * * * *`), `kma_short_forecast_sync`(단기예보, KMA 실제 발표시각인
`15 2,5,8,11,14,17,20,23 * * *`), `kma_mid_forecast_sync`(중기예보,
`30 6,18 * * *`), `kma_weather_alerts_sync`(특보, hourly `5 * * * *`). 예전에는 다섯
dataset이 `kma_weather_sync` 하나의 asset과 `kma_weather_bundle`이라는 하나의
`weather_sync_runs` row를 공유했다 — 단기예보는 3시간에 한 번만 새 응답이 나오는데도
매시간 재요청했고(quota의 3분의 2가 낭비), 다섯 dataset 중 하나가 실패해도 admin
화면에는 뭉뚱그려진 run 하나만 보여 어느 dataset이 문제인지 알 수 없었다. 지금은
dataset마다 자기 cadence로만 요청하고 자기 `dataset_key`로 추적된다.

target은 활성 DB catalog가 정본이며 env `TARGETS`는 bootstrap 신규 row와 provider
속성을 보완한다. DB에서 disabled 된 id는 env가 재활성화할 수 없다. lat/lon만 있는
target은 `kma.to_grid`로 nx/ny를 계산한다. AirKorea 등 다른 provider의 측정소
anchor는 `_is_kma_target_location`이 기본적으로 KMA target에서 제외한다 —
`metadata.kma_opt_in=true`로 관리자가 명시적으로 opt-in한 행만 예외다.

격자 job(초단기실황·초단기예보·단기예보)은 명시 target이 덮지 않은 격자 일부를
`_station_grid_fill`로 측정소 anchor에서 채운다. 명시 target이 하나도 없는 새
catalog(2026-09-20 새 DB 이후 운영이 그랬다)에서 세 격자 job과 특보 job이 빈 target으로
매번 실패했기 때문이다. 격자 선택은 측정소가 아니라 KMA 격자 중심 좌표로 외부 provider
cap과 같은 `spatially_even_subset` 순서를 쓴다 — 측정소가 생기고 사라져도(AirKorea는
주소만 바뀌어도 id를 새로 만든다) 고른 격자가 흔들리지 않는다. 고른 격자 위 측정소는 모두
같은 응답을 fan-out으로 받는다. **명시 target의 격자 위 측정소도 마찬가지다** — 그 격자는
어차피 요청하므로 채우기 예산을 쓰지 않고 응답만 함께 받는다(전국 중기 지역 target 21곳 중
19곳이 측정소가 있는 격자였고, 처음 구현은 그 측정소들을 빼서 10곳이 매시 KMA를 잃었다).
실행 로그에 `station fill: X of Y stations on Z of W grids (N shared with explicit
targets)` 한 줄을 남긴다.

**채우기 예산은 따로 둔다.** `KMA_STATION_FILL_MAX_GRIDS`(기본 150)가 채우기 격자 수의
상한이고, 명시 target과 합친 전체는 여전히 `MAX_GRIDS_PER_RUN`(기본 300)을 넘지 않는다.
`MAX_GRIDS_PER_RUN`은 상한이지 소진 목표가 아니다. data.go.kr 한도는 서비스가 아니라
오퍼레이션마다 일 10,000건이고, **같은 서비스 키를 Map과 로컬 python-kma-api 테스트도
쓴다**. 매시 도는 두 오퍼레이션 기준:

| 채우기 격자 | 초단기실황/초단기예보 (각 24회/일) | 10,000 대비 | 단기예보 (8회/일) |
| --- | --- | --- | --- |
| 150 (기본) | 3,600/일 | 36% | 1,200/일 |
| 300 | 7,200/일 | 72% | 2,400/일 |

재시도(`PROVIDER_RETRIES`, 기본 3)는 일시 오류에서만 호출을 더 쓰므로 36%는 키를 나눠
쓰는 다른 사용처와 재시도 몫으로 남긴다. 명시 target은 이 위에 더해진다.

**한 run의 크기.** KMA는 모든 응답이 유효할 때만 publish를 시작하고, publish는
location chunk 단위의 짧은 transaction으로 나눈다(아래 단락). 크기는 채우기 예산으로
묶는다. 2026-10-01 운영 catalog(측정소
1,429곳, 1,074격자, 격자당 최대 7곳) 실측으로 150격자는 측정소 196곳, 300격자는 385곳을
덮는다. 단기예보 응답이 격자당 약 900행이면 150격자 run은 약 18만 fact(fan-out 포함),
메모리 약 0.36 GiB, 300격자는 약 0.73 GiB다. fact 수는 격자 수 × 격자당 행 수 × 격자당
측정소 수로 묶이고, 최종 상한은 `MAX_VALUES_PER_RUN`이다.

특보만은 예외로, `_kma_alert_targets`가 opt-in 여부와 무관하게 활성 location 전체를
대상으로 한다 — 지도 marker에는 특보가 항상 보여야 하기 때문이다(채우기는 계산하지
않는다). 빈 target 검사는 dataset마다 자기가 읽는 target으로 한다: 특보는 alert target,
격자 job은 격자 target이다. 중기예보는 지역 코드가 있는 target이 없으면 실패하지 않고
`skipped: true`와 이유를 output metadata로 남긴다 — 키 없는 외부 provider와 같은 규칙이다.
측정소에는 중기 지역 코드가 없으므로 env `TARGETS`나 관리자 catalog에 지정해야 한다.

중기예보 target은 `mid_land_region_code`(예: `11B00000`)와
`mid_temperature_region_code`(예: `11B10101`)를 모두 설정한다. 과거 설정의
`mid_region_code`는 두 API에 같은 코드를 쓰는 legacy alias로만 유지한다. 각 지역
응답은 region별로 한 번 호출하고 `mid-land:<code>`/`mid-temperature:<code>` source
entity로 추적한다.

각 grid의 nowcast/ultra-short/short (필요하면 mid) 응답을 남은 row/fact budget
안에서 bounded stage한다. 모든 응답이 유효하고 non-empty일 때만 publish를 시작한다 —
N번째 grid 실패, quota/4xx, wrong grid, malformed date는 아무 fact도 publish하지 않는다.
publish는 **location chunk 단위의 짧은 transaction**이다: 격자·중기 fact는
`GRID_PUBLISH_LOCATIONS`(50)곳·`GRID_PUBLISH_VALUES`(5,000) fact 이하, 특보 fact는
`ALERT_PUBLISH_LOCATIONS`(50)곳씩(한 location의 fact는 나누지 않는다). 한 transaction은
그 chunk의 location lock만 쥔다. 예전에는 run 전체를 한 transaction으로 publish해 그
동안 run이 닿는 모든 location lock을 쥐었다 — 특보는 2026-10-01에 1,450곳을 한 시간
넘게, 단기예보는 2026-10-02에 1,103곳을 4시간 3분 동안 쥐고 다른 KMA job을 2시간 넘게
세웠다. 그래서 publish **도중**의 실패(DB 오류, lease 상실)에 대해서만 all-or-nothing을
포기한다: 앞 chunk는 publish된 채 남고 run은 그때까지의 `values_loaded`로 failed가 된다.
publish된 chunk는 그 location들에 대한 온전한 응답의 fact 전부이고, 다음 run의 replay는
이미 있는 fact에 대해 no-op이다. 각 chunk는 자기 fact가 인용하는 source record를 함께
싣고 가므로 매 transaction이 run row를 잠그고 lease 소유를 다시 확인한다. 어떤 fact도
인용하지 않는 source(어느 location에도 맞지 않은 특보)는 run을 끝내는 마지막
transaction에 기록된다.

repository 쪽 ingest transaction은 lock 대기를 `3 × deadlock_timeout`(partition DDL과
같은 `ddl_lock_timeout_ms`)으로 묶고, lock 경합에서 지면 rollback 뒤 3초 간격으로 최대
20회 다시 시도한다 — 오래 쥔 holder 뒤에 몇 시간씩 줄 서지 않는다. statement timeout은
두지 않는다: transaction 길이는 chunk 크기가 묶고, n150처럼 디스크 대기가 큰 호스트에서
정상 chunk를 거짓 실패시키지 않기 위해서다. location·source advisory lock은
transaction당 한 번만 잡고(예전엔 fact마다 네 번 왕복), replay 확인은 1,000건씩 묶은
primary-key 조회, current projection 갱신은 `INSERT ... ON CONFLICT DO UPDATE ... WHERE
newer` 한 문장(1,000행씩)이다 — 비교·교체가 문장 안에서 행 단위로 원자적이다.

**특보는 잡힌 location을 기다리지 않고 건너뛴다.** 2026-10-03 `kma_weather_alerts`는
매시 run 대부분이 `location:airkorea-station-…` 하나의 lock timeout으로 실패했다(실패
run 대부분이 약 125초 = 재시도 예산 20 × (3초 대기 + 3초 휴지)). 다른 job의 publish
chunk가 n150 디스크 대기 아래서 그 location을 예산보다 오래 쥐었기 때문이다(같은 날
단기예보 run은 20만 fact에 3.3시간, chunk당 수 분). 특보 fact는 다음 매시 run이 같은
특보를 다시 받아 오므로 한 location을 한 tick 늦게 쓰는 비용이 run 전체보다 훨씬 작다.
그래서 특보 chunk는 `ingest_skip_locked`로 publish한다: location advisory lock을
**정렬 순서대로 `pg_try_advisory_xact_lock`으로만** 잡고(기다리지 않으므로 deadlock의
대기 쪽이 될 수 없다 — lock 순서 보장은 그대로), 잡힌 location의 fact는 빼고 나머지를
같은 transaction으로 publish한다. 다른 lock(run row·source·projection)에서 재시도
예산을 다 쓴 chunk는 통째로 건너뛴다. 건너뛴 location은 `ALERT_SKIP_RETRY_ROUNDS`(2)번,
`ALERT_SKIP_RETRY_SECONDS`(15초) 간격으로 다시 시도하고, 그래도 남으면 다음 tick에
맡긴다. run은 success이고 건너뛴 수와 ID(최대 100개)를 run의 `error` 칸에 메모로 남기며
(AirKorea의 "N개 측정소 요청 실패"와 같은 방식), 로그에는 수와 앞 5개 ID를 남긴다.
run이 실패하는 것은 특보 location을 **하나도** publish하지 못했을 때와 lock 아닌 오류뿐이다.
같은 location이 `ALERT_STARVED_RUNS`(3) run 연속 건너뛰어지면(앞 두 run의 메모와 교집합)
starvation 경고를 로그와 asset metadata(`alert_locations_starved`)에 남긴다.

response metadata(endpoint, request
params, status when available)도 raw payload에 포함한다. durable cursor는 아직
없으므로 source idempotency가 반복 응답의 저장 비용을 제어하고, 호출 비용을 줄이는
cursor는 후속 범위다.

외부 provider는 **provider마다 하나씩** asset/job 경계를 갖는다
(`{provider}_weather_sync` / `{provider}_weather_job`). 하나의 asset이 모든 외부
provider를 순회하던 이전 구조에서는 한 provider의 응답이 멈추면 나머지 전부가 같은
run 안에서 함께 멈췄고, 그 run이 동시 실행 슬롯을 계속 점유해 KMA·에어코리아·지역별
수집까지 큐에 쌓였다. 분리된 지금은 멈춘 provider가 자기 run 하나만 소비한다.
job들은 같은 `:15` tick에 **3시간마다** 예약되지만 `kortravelweather/run_group`
태그와 `deploy/dagster.yaml`의 `tag_concurrency_limits`가 동시 실행 수를
제한하므로 한꺼번에 슬롯을 가져가지 않는다.

## 무료 quota가 수집 범위를 정한다

외부 provider는 KMA의 보조 비교군이므로, 무료 한도를 넘겨가며 전 지점을 매시간
긁는 것은 이득이 없다. 넘기면 조용히 실패하지도 않는다 — vendor가 throttle을
걸면 요청당 0.3초가 15초가 되고, 한 번의 sweep이 자기 스케줄보다 오래 살아남아
run 큐를 채운다. 실제로 그렇게 성공한 run 하나가 6.6시간 걸렸고, 그 동안 국내
소스(해수욕장·산악·고속도로)는 19시간 동안 갱신되지 못했다.

그래서 두 개의 설정이 범위를 정한다. 둘 다 각 vendor가 공개한 무료 한도에서
20% 마진을 뺀 뒤, dataset 수와 하루 8회(3시간 주기)로 나눈 값이다.

- `KOR_TRAVEL_WEATHER_PROVIDER_LOCATION_CAPS` — provider가 훑을 위치 수.
  목록에 없으면 전 지점을 훑는다. 줄일 때는
  `providers/sampling.py`의 격자 표본으로 **전국에 고르게** 남긴다. id 순으로
  앞에서 자르면 특정 지역만 남아 비교군으로서의 값이 사라진다.
- `KOR_TRAVEL_WEATHER_PROVIDER_MIN_REQUEST_INTERVAL_SECONDS` — 요청 간 최소
  간격. 월 한도가 유일한 천장이 아니다. OpenWeatherMap은 분당 60건도 함께
  걸어 두는데, 응답이 정상 속도로 돌아오면 sweep이 분당 200건으로 달려 월
  예산이 한참 남은 채로 throttle을 맞는다.

resource가 `KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS`와 provider별 secret/base URL을 읽고,
활성 location catalog를 `ProviderLocation`으로 변환한다.

응답은 **배치 단위로 stage하며 publish**한다(`_PUBLISH_BATCH_VALUES`). 예전에는 sweep
전체를 모은 뒤 한 번에 publish해서 "중간에 실패하면 아무것도 publish되지 않는" 성질이
있었지만, 그 대가로 openweathermap의 forecast 스텝이 8.14GiB를 점유해 호스트를 스왑으로
몰아넣었고 다른 provider의 요청이 0.3초에서 9.7초로 느려졌다. 지금은 배치 하나가
원자적이고, 중간에 실패하면 **이미 publish된 배치는 남는다**. fact는 immutable이고
`source_record_key`로 식별되므로 부분 sweep은 단지 갱신된 지점이 적다는 뜻이며, 재시도는
빠진 것만 채운다. 실패한 run은 그때까지 적재한 건수를 기록한다.

`_PUBLISH_BATCH_VALUES`(50,000)는 **메모리** 상한일 뿐이다. stage된 배치는 KMA와 같은
`chunked_publish` location chunk(50곳·5,000 fact 이하, location 단위로만 나눔)로
publish한다 — 50,000 fact 한 transaction은 그 ~37곳의 lock을 37 GB fact table 위에서
끝날 때까지 쥐었고, ingest lock timeout이 생긴 뒤로는 그 뒤에 선 KMA chunk가 기다리는
대신 실패한다. AirKorea 측정값과 지역 provider(해수욕장·산악·고속도로)도 같은 chunk로
publish한다. run을 끝내는 transaction은 어떤 fact도 인용하지 않는 source만 싣고 location
lock을 잡지 않는다.

**run의 `values_loaded`는 chunk가 함께 기록한다.** 각 chunk transaction은 run row를 잠근 채
자기가 적재한 수를 더하므로, stale-run reaper가 먼저 run을 failed로 만들어 collector의
failed finish가 no-op이 돼도 row에는 실제로 commit된 수가 남는다. `finish_sync_run`의
`values_loaded`를 생략하면 그 기록을 유지한다. publish는 동기 repository 호출이고 lock
경합 재시도의 pause도 blocking이므로, event loop 위에서 publish하는 collector(KMA,
AirKorea)는 `asyncio.to_thread`로 부른다.

중간 target의 timeout·429·4xx·schema 오류는 여전히 run을 실패시킨다.
provider가 반환한 `source_record_key`는 response payload와 redacted request
metadata의 hash라서 같은 응답 replay는 no-op이며, 수정 응답은 새 source revision이다.
KMA/외부 실행은 provider 응답을 bounded iterable로 소비하고 run heartbeat를
그룹/대상 경계에서 갱신한다. stale 회수는 `heartbeat_at`(legacy row는
`started_at` fallback)을 기준으로 하므로 정상적인 장기 실행을 조기에 회수하지 않는다.

## 멈춘 호출의 경계

HTTP 시도 단위 timeout은 `request_json`이 이미 보장한다. 그러나 바이트도 오류도
돌려주지 않는 연결은 어떤 시도도 끝내지 못하므로 retry 예산 자체가 소진되지 않는다.
실제로 이 상태의 run 하나가 18시간 동안 살아 있었고, 프로세스가 살아 있었기 때문에
Dagster의 liveness 검사로는 구분할 수 없었다. 그래서 경계가 두 겹이다.

- `run_external_weather_sync`는 provider 호출마다 벽시계 deadline을 건다
  (timeout × 시도 횟수 + 60초). 멈춘 호출은 그 provider의 run만 실패시키고 끝난다.
- `deploy/dagster.yaml`의 `run_monitoring.max_runtime_seconds`는 이유와 무관하게
  run 전체의 벽시계 상한이다. 위 deadline이 닿지 못하는 지점에서 멈추더라도
  슬롯은 반드시 반환된다.

이 상한(57600초)은 instance 설정에만 두지 않고 run 자체에도 싣는다. 이름 붙은
job은 모두 `dagster/max_runtime` 태그(`RUN_MAX_RUNTIME_SECONDS`)를 달고 만들어지므로,
여러 프로젝트가 같이 쓰는 Dagster instance에서도 weather run은 이 값을 그대로 가진다.

예외는 `__ASSET_JOB`이다. Dagster UI의 asset graph에서 **Materialize**로 바로 띄운
run은 Dagster가 암묵적으로 만드는 `__ASSET_JOB`으로 실행되고, 이 job은
`Definitions`의 태그를 받지 않는다. 그래서 공용 instance에서는 그런 run이 **호스트
기본 `max_runtime_seconds`**를 받는다. 그 값은 다른 프로젝트 기준으로 정해지므로
weather의 긴 sweep(측정상 ~13시간인 weatherapi 등 외부 provider 전체 수집)은
중간에 취소될 수 있다. 긴 수집은 asset graph가 아니라 이름 붙은 job
(`<provider>_weather_job`, `kma_*_job`, `airkorea_weather_job` 등)의 Launchpad로 띄운다.
짧은 단건 재적재는 asset graph로 띄워도 된다.
