# Dagster boundary

`kma_weather_sync`는 hourly (`0 * * * *`, Asia/Seoul) asset이다. target은 활성 DB
catalog가 정본이며 env `TARGETS`는 bootstrap 신규 row와 provider 속성을 보완한다.
DB에서 disabled 된 id는 env가 재활성화할 수 없다. lat/lon만 있는 target은
`kma.to_grid`로 nx/ny를 계산한다.

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
활성 location catalog를 `ProviderLocation`으로 변환한다. provider별 응답은 모두 stage한
후 `WeatherRepository.publish_and_finish`에 전달하므로, 중간 target의 timeout·429·4xx·
schema 오류가 발생하면 failed run만 남고 기존 KMA 또는 external fact는 publish되지
않는다. provider가 반환한 `source_record_key`는 response payload와 redacted request
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
