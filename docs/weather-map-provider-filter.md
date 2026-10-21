# 날씨 지도 제공처 다중 선택

2026-10-22 KST 기준 [제품 PR #126](https://github.com/digitie/kor-travel-weather/pull/126)의 기능·성능·운영 검증 기록이다.

## 사용 방법과 범위

날씨 지도 검색 옆의 소스 필터에서 KMA·OpenWeatherMap·WeatherAPI 등 데이터 제공처를 여러 개 선택한다. 관측소가 어느 기관에서 등록됐는지와는 별개이며, 선택한 제공처의 저장 값이 있는 위치를 OR로 표시한다. 기존 위치 검색은 AND로 함께 적용한다. 지도 마커·목록·최근 관측값·예보에 동일한 조건을 사용한다.

- 기본은 모든 소스다. 전체 선택 또는 모든 체크 재선택은 기존 전체 위치 카탈로그로 돌아간다.
- 전체 해제는 위치와 상세를 비우고 추가 데이터 요청을 보내지 않는다.
- 일부 제공처를 선택하면 해당 제공처의 현재 projection에 값이 있는 활성 위치만 표시한다. 이 기준은 데이터가 없는 위치도 포함할 수 있는 기본 전체 카탈로그와 구분한다.
- 제공처 필터가 변경되면 이전 요청을 취소하고, 이전 제공처의 위치·상세·마커 요약을 섞지 않는다. 마커 캐시는 제공처 조합별로 구분한다.
- shadcn/ui Radix 체크 메뉴를 기존 UI 토큰에 맞춰 사용한다. 방향키·Space/Enter·Escape, 체크 유지, 포커스 복귀와 모바일 화면을 지원한다.

## API와 조회 구조

공개 `locations`, `latest`, `forecast`, `markers` API에서 반복 `provider` query를 받는다. 예시는 `?provider=weatherapi&provider=openweathermap`이다. 최대 32개이며 각 값은 1~120자다. 생략하면 기존 동작을 유지하고, 일치하지 않는 제공처는 빈 결과를 반환한다. OpenAPI와 TypeScript 요청 함수를 함께 갱신했다.

위치 카탈로그는 current projection의 제공처 존재 여부를 pagination·count 전에 적용한다. 관측·예보도 제공처 조건을 SQL LIMIT 전에 적용해, 먼저 자른 결과를 브라우저에서 거르면서 값이 사라지는 문제를 피한다.

마커 일반 값은 현재 projection의 `ix_weather_current_values_provider_marker` 부분 인덱스에서 `(location_id, provider, metric_key)`마다 1개를 읽는다. 선택 제공처 사이에서도 관측/nowcast 우선, 이후 known_at/source_record_key/value_id 정렬로 지표별 대표값을 유지한다. 지도에 필요한 TEMP/T1H/TMP/WEATHER_CODE/SKY/PTY만 인덱스에 포함한다.

특보는 station·sequence가 다른 이벤트를 합치지 않도록 기존 fact 이벤트별 revision과 위치별 상한 80을 유지한다. key를 먼저 확정한 뒤 paired unnest의 정확한 `(value_id, known_at)` PK LATERAL 조회와 날짜 범위로 전체 fact를 읽는다. payload를 잘라내지 않는다. 무필터 조회 경로는 유지한다.

## 인덱스 적용과 복구

migration `0022_provider_marker_index`는 현재 projection에 부분 인덱스 1개를 CONCURRENTLY 생성한다. 인덱스 크기는 적용 당시 121,593,856 bytes(약116MiB)였다. 빌드·이미지·소스·데이터·작업 증거는 Odroid NVMe를 사용했다.

첫 생성은 기존 트랜잭션을 기다리는 과정에서 세션의 lock_timeout 5초에 걸려 INVALID 인덱스를 남겼다. 최초 증거에서 이 이름의 인덱스가 없었고 이후 정의가 이 작업의 DDL과 동일함을 확인한 뒤, 미완성 인덱스만 CONCURRENTLY 제거했다. 해당 생성 세션에 한해 대기 한도를 30초로 늘리고 statement_timeout 15분, maintenance_work_mem 64MiB, parallel maintenance worker 0으로 재시도하여 약221초에 완료했다. 전역 DB 설정과 다른 서비스는 변경하지 않았다.

재시도 시에는 index 이름·대상 테이블·정의·indisvalid·alembic revision을 먼저 확인한다. 다른 이름/정의이거나 이미 유효한 index를 임의 삭제하지 않는다. 실패한 시도와 복구 기록은 원본대로 보존한다.

## 실측과 한계

운영 DB를 읽기 전용으로 사용하고 각 SQL에 5초 제한을 적용했다. 첫 단순 제공처 조건 경로에서는 60곳 마커 조회가 KMA·OpenWeatherMap·OpenWeatherMap+WeatherAPI 모두 제한을 초과했다. 이후 지표별 부분 인덱스와 특보 key 확정 후 PK 조회를 적용했다.

최종 표본은 별도 768MiB 제한 컨테이너에서 실행했으며 UI 이미지 빌드와 수집이 병행됐다. Python 프로세스 최대 RSS는 92,816KiB(약91MiB)였다. 이는 API 전체·DB·호스트 메모리 사용량을 의미하지 않는다.

| 제공처 | 활성 위치 수 | 마커 60곳 전체 처리 | 단일 위치 최근 값 200행 |
| --- | --- | --- | --- |
| KMA | 1,450 | 6,769ms | 1,135ms |
| OpenWeatherMap | 500 | 1,485ms | 538ms |
| OpenWeatherMap + WeatherAPI | 500 | 2,685ms | 456ms |

최종 표본의 각 SQL은 5초 내 완료했다. KMA 전체 6.769초는 여러 SQL·전송·Python 처리를 합친 시간이므로 SQL별 제한과 구분한다. 부하·캐시를 통제한 반복 p95 측정이나 전체 서버 CPU 감소율은 아니다. 인덱스 쓰기 비용과 추가 디스크 사용도 있으므로 효과를 전체 수집 성능으로 확대 해석하지 않는다.

[이전 읽기 측정](reviews/evidence/2026-10-22-weather-provider-filter-before.json), [최종 읽기 측정](reviews/evidence/2026-10-22-weather-provider-filter-after.json)에 실제 응답 수·SQL별 시간·전체 시간을 남겼다.

## 검증 및 운영 배포

최종 제품 커밋은 `eabcece0610dacf3bf863f1191099d90b4bdb999`다. 두 명의 독립 적대적 리뷰에서 열린 P0/P1/P2는 없다. 초기 지표별 대표값·특보 누락 지적을 반영하고 TEMP 81개에도 SKY가 유지되는지, 동일 시각의 특보 21개가 보존되는지, 다른 날짜의 같은 UUID가 filtered observed/fallback/alert에 섞이지 않는지 검증했다. 실제 PostgreSQL EXPLAIN의 인덱스 3개 prefix 조건도 확인했다. 작은 fixture에서 enable_seqscan=off로 확인한 인덱스 사용 가능성과 위 운영 실측을 구분한다.

기능 PR [#126](https://github.com/digitie/kor-travel-weather/pull/126)은 `bdeeae6df767d0b7657875f071434d1307559e7a`로 머지했다. 최종 제품 커밋 CI 두 실행(39687822655, 39687814870)에서 Python 709개, 프론트엔드 218개와 Ruff·타입·lint·빌드·OpenAPI 검사가 통과했다.

스테이징의 실제 로그인·VWorld 지도에서 14개 UI 시나리오를 통과했다. 전체 선택/해제, 키보드 열기·Space 선택·Escape 초점 복귀, 단일/복수 제공처, 제공처와 검색의 조합, 전체 재선택 정규화, 빠른 선택 변경, 390px 모바일 메뉴, 실제 로그아웃을 포함한다. 단일·복수 제공처의 마커 API 응답도 직접 검사해 선택 외 제공처가 없음을 확인했다. JavaScript 오류와 예상하지 않은 HTTP 오류는 없었다. 로그아웃 직후 이미 전송된 마커 요청 2개의 401은 세션 폐기에 따른 정상 거절로 별도 기록했다. [스테이징 UI 증거](reviews/evidence/2026-10-22-weather-provider-filter-stage-e2e.json)

운영 API·웹에는 ARM64 이미지 `codex-local/weather126-api:eabcece0`, `codex-local/weather126-web:eabcece0`을 배포했다. 공개 `/health`, `/version`, `/login`은 모두 HTTP 200이며 `/version`은 최종 제품 커밋과 일치한다. 공유 compose에서 두 이미지 값만 바꾸고 `up -d --no-deps`로 적용했다. Geo·Transport·공유 PostgreSQL·Dagster 및 Weather code-server 등 10개 컨테이너의 ID와 실행 상태를 보존했다. Dagster daemon과 code-server는 계속 실행 중이다. [배포·인덱스 증거](reviews/evidence/2026-10-22-weather-provider-filter-deployment.json)

운영 공개 HTTPS 주소에서도 같은 14개 시나리오를 모두 통과했다. 단일/복수 제공처의 실제 마커 응답 2건을 검사했고 VWorld 요청 9건은 모두 성공했다. JavaScript 오류와 예상하지 않은 HTTP 오류는 없었다. 실제 로그아웃 뒤 남아 있던 마커 요청 1건의 정상 401만 따로 기록했다. 데스크톱 및 390px 모바일 캡처도 확인했다. 테스트용 API·웹 컨테이너와 임시 환경 파일은 정리했고 운영 롤백 이미지·compose 백업·배포 영수증은 보존했다. [운영 UI 증거](reviews/evidence/2026-10-22-weather-provider-filter-prod-e2e.json)
