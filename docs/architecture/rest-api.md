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
