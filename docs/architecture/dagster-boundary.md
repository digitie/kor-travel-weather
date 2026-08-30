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

각 grid의 nowcast/ultra-short/short (필요하면 mid) 응답을 메모리에 stage한다. 모든
응답이 유효하고 non-empty일 때만 full raw source record와 normalized facts를 한
`ingest_batch` transaction으로 publish한다. N번째 grid 실패, quota/4xx, wrong grid,
malformed date는 이전 fact를 변경하지 않는다. response metadata(endpoint, request
params, status when available)도 raw payload에 포함한다. durable cursor는 아직
없으므로 source idempotency가 반복 응답의 저장 비용을 제어하고, 호출 비용을 줄이는
cursor는 후속 범위다.

외부 provider는 별도 `external_weather_sync` asset/job 경계를 사용한다. resource가
`KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS`와 provider별 secret/base URL을 읽고, 활성
location catalog를 `ProviderLocation`으로 변환한다. provider별 응답은 모두 stage한
후 `WeatherRepository.publish_and_finish`에 전달하므로, 중간 target의 timeout·429·4xx·
schema 오류가 발생하면 failed run만 남고 기존 KMA 또는 external fact는 publish되지
않는다. provider가 반환한 `source_record_key`는 response payload와 redacted request
metadata의 hash라서 같은 응답 replay는 no-op이며, 수정 응답은 새 source revision이다.
