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

`weather_values`와 `weather_source_records`는 PostgreSQL DB trigger로 UPDATE/DELETE를 막는다.
PostgreSQL `timestamptz`가 timezone-aware 값의 저장·조회 기준이다.


KMA 단기예보의 미래·최근6시간 값은 원 fact가 없고 원 clock이 retention gap으로 저장 불가한
경우에만 재입장 시각을 known_at으로 사용한다. source.fetched_at으로 원 clock을 복원할 수 있고
collected_at/issued_at/valid_at/payload/lineage는 불변이다. 정상 DEFAULT 첫 입장과
이미 보유한 최초 복합키는 유지한다. 자세한 예외는 [ADR-072](../adr/072-weather-bitemporal.md)를 따른다.

## 좌표 기반 파생 주소

`0021_reverse_addresses`에서 `reverse_address_entries`와 `reverse_address_control`을 추가한다.
`provider_id`·`target_key` 복합 식별자로 현재 좌표의 주소·행정구역·확인일·다음 갱신일과 오류/backoff를 분리 보관하고,
제어 행은 수동 cycle·커서·만료 lease를 보관한다. 원천 주소·좌표·관리자 설정과 과거 관측은 변경하지 않는다.
달력 6개월 갱신, VWorld 우선·Kakao fallback, 수동 실행과 복구 계약은 [구현 가이드](../reverse-address-refresh.md)를 따른다.
