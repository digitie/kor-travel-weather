# REST API contract

운영 Base URL은 `https://weather-api.digitie.mywire.org`이며, 로컬 Compose Base URL은
`http://127.0.0.1:14101`이다. 성공 응답은 `{data, meta}` envelope다.
목록의 `meta.page`에는 `limit`, `offset`, `returned`, `total`이 포함된다.
`meta.request_id`는 `X-Request-ID`와 같다. raw payload는 public response에 노출하지 않고
`source_record_key`만 반환한다.

## Public

- `GET /health`, `GET /version`
- `GET /v1/weather/locations?search=&limit=&offset=` (항상 enabled만)
- `GET /v1/weather/locations/{location_id}`
- `GET /v1/weather/locations/{location_id}/latest?limit=`
- `GET /v1/weather/locations/{location_id}/forecast?from=&to=&dataset_key=&metric_key=&history=&limit=`
- `GET /v1/weather/nearby?lat=&lon=&radius_km=&limit=` (각 결과에 측정소,
  `latest`, `forecast`, `alerts` 포함)
- `GET /v1/weather/resolve?lat=&lon=&radius_km=` (가장 가까운 AirKorea 측정소의
  모든 provider bundle)

`from > to`, timezone 없는 ISO-8601, 대한민국 밖 좌표, 과도한 limit은 422다.
disabled location은 404로 숨긴다. forecast의 기본 응답은 current projection이고
`history=true`가 명시된 경우에만 correction revision이 포함된다.

## Admin

`X-Admin-Token`이 설정된 환경에서는 constant-time 비교로 보호한다.
OpenAPI의 `AdminToken` api-key security scheme(`x-admin-token`)로 생성 client가
헤더를 주입할 수 있다.

- `GET /v1/admin/locations`
- `POST /v1/admin/locations`
- `PATCH /v1/admin/locations/{location_id}` (`enabled=false` 비활성화)
- `GET /v1/admin/sync-runs`
- `GET /v1/admin/sync-runs/{run_id}/sources` (raw rows를 제외한 redacted lineage)

admin에도 DELETE는 없다. 운영 설정에서 token이 없으면 app startup이 실패한다.
오류는 `application/problem+json`의 `status`, `code`, `request_id`를 사용한다.

## Consumer adapter boundary

이 서비스의 canonical key는 `location_id`다. `kor-travel-map` feature는 자체
feature identity와 좌표를 유지하고, `/resolve`로 가장 가까운 AirKorea 측정소의
all-source bundle을 받는다. PinVi도 같은 adapter를 사용한다. 지도 전용
`/v1/features/*` 경로는 이 source가 소유하지 않지만, KMA 특보는 `alerts`와
지도 marker 상태로 제공한다.

## current·daily export의 검증 완료 결과 재사용

API만 승인 상태 조회 시작 뒤 최대60초의 완성 페이지를 재사용한다. 인증은 매 요청 수행하며 provider/limit/cursor/days/location와 설정·서명키 범위를 구분한다. x-export-read는 generated/reused다. generated_at은 wire 생성 시각이고 source_collected_at·snapshot_generated_at·as_of는 실제 검증한 데이터의 시각을 유지한다. 승인/실패·derived summary 반영은 이60초 한도 안에 지연될 수 있다.

cache hit는 snapshot TTL·manifest fingerprint·세대/plan/head와 실제 사용 파일 hash/count를 확인한다. 만료/재검산/갱신 실패를 이전 정상200/304로 연장하지 않는다. 주기적 전체 재생성은 없으며 요청이 있는 miss만 생성하고 같은 키는 합친다. 최초 미준비나 상한 밖 요청은 원 경로다. current의 긴 metadata 구성은 파일 guard 밖이고 새 pin의 파일 존재 검사와 DB commit만 GC와 직렬화한다. 참조 INSERT는512개 묶음으로 같은 TX에서 처리한다. SDK 기본 경로·SQL5초·파일 FD 검산은 유지한다. [근거와 실제 수용 단계](../reports/2026-11-08-aggressive-export-read.md).
