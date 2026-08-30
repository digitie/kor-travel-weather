# Data model

## Location

`weather_locations.location_id`는 consumer가 참조하는 안정적인 catalog key다.
좌표는 WGS84 범위(대한민국 33–43N, 124–132E), `nx`/`ny`는 KMA grid다. admin은
삭제하지 않고 `enabled=false`로 비활성화한다. 이미 fact가 있는 location의 좌표/grid
변경은 과거 fact 의미를 바꾸므로 API가 거부한다.

## Source lineage

`weather_source_records`에는 provider 응답 전체(raw rows와 endpoint/request metadata)를
canonical JSON과 SHA-256으로 저장한다. 같은 `source_record_key`의 payload/메타데이터
변경은 충돌로 거부한다. `weather_sync_run_sources`가 실행과 응답을 다대다로 연결하므로
replay도 원본 lineage를 잃지 않는다.

## Fact identity and time axes

ADR-089 축은 `(location, provider, dataset, weather_domain, forecast_style, metric_key,
target_at, source_record_key)`다. `value_id`는 이 축의 hash이며 source response 수정은
새 key로 append된다. `issued_at`, `valid_at`, `observed_at`은 provider 원본 시각이고
`known_at`은 수신 시각이다. public latest/timeline은 logical target별 최신 revision 하나를
선택하며 `history=true`일 때만 모든 revision을 반환한다.

`weather_values`와 `weather_source_records`는 DB trigger로 UPDATE/DELETE를 막는다.
SQLite에서도 timezone-aware 값은 UTC로 round-trip한다.
