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
`metadata.kma_opt_in=true`로 관리자가 명시적으로 opt-in한 행만 예외다. 명시 target이
쓰고 남은 격자 예산(`MAX_GRIDS_PER_RUN`)은 `_station_grid_fill`이 측정소 anchor로
채운다 — 아직 덮이지 않은 격자마다 대표 측정소 하나를 외부 provider cap과 같은
`spatially_even_subset` 순서로 골라 남은 개수만큼 취하고, 고른 격자의 측정소는 모두
fan-out으로 함께 받는다. 그래서 명시 target이 하나도 없는 새 catalog에서도(2026-09-20
새 DB 이후 운영이 그랬다) 격자 job이 빈 target으로 매번 실패하지 않으며, 격자 수는
상한을 넘지 않는다. 특보만은 예외로, `_kma_alert_targets`가 opt-in 여부와 무관하게
활성 location 전체를 대상으로 한다 — 지도 marker에는 특보가 항상 보여야 하기 때문이다.
빈 target 검사는 dataset마다 자기가 읽는 target으로 한다: 특보는 alert target, 중기예보는
지역 코드가 있는 target이다. 지역 코드가 있는 target이 없으면 중기예보 job은 아무것도
받지 않고 성공하는 대신 그 이유로 실패한다 — 측정소에는 중기 지역 코드가 없으므로 env
`TARGETS`나 관리자 catalog에 지정해야 한다.

중기예보 target은 `mid_land_region_code`(예: `11B00000`)와
`mid_temperature_region_code`(예: `11B10101`)를 모두 설정한다. 과거 설정의
`mid_region_code`는 두 API에 같은 코드를 쓰는 legacy alias로만 유지한다. 각 지역
응답은 region별로 한 번 호출하고 `mid-land:<code>`/`mid-temperature:<code>` source
entity로 추적한다.

각 grid의 nowcast/ultra-short/short (필요하면 mid) 응답을 남은 row/fact budget
안에서 bounded stage한다. 모든 응답이 유효하고 non-empty일 때만 full raw source record와 normalized facts를 한
`ingest_batch` transaction으로 publish한다. N번째 grid 실패, quota/4xx, wrong grid,
malformed date는 이전 fact를 변경하지 않는다. response metadata(endpoint, request
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
