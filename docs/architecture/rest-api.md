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

## forecasts(W3) meta의 멤버십 digest와 clock epoch (2026-11-11, Map ADR-122)

`/v1/service/exports/forecasts`의 모든 페이지 `meta`에 두 값을 더한다(추가 필드, 기존 소비자 호환).

- `membership_digest`: 얼린 manifest 전체의 `(location_id, provider, published_at)` 집합 digest다. 시간 필터
  (`target_at > now`) **전** 집합이고 `since`와 무관하며 한 walk의 모든 페이지가 같다. 정의: 행을 정렬하고
  (문자열은 code point 순) `published_at`을 UTC·마이크로초 6자리·`Z`로 적은
  `[[location_id, provider, "YYYY-MM-DDTHH:MM:SS.ffffffZ"], ...]`를 구분자 `,`·`:`, `ensure_ascii=False`로 JSON
  직렬화한 UTF-8의 SHA-256(`sha256:` 접두). 중복 행은 그대로 센다. 중기는 위치마다 한 행(육상·기온 쌍의 큰
  `published_at`)이다. 소비자는 병합 상태로 같은 값을 계산해 다르면 전량을 받는다 — tombstone 없는 제거·놓친 추가·
  재공개됐지만 미래 시각이 없어 빠진 위치를 잡는다.
- `clock_epoch`: `next_since`를 발급한 DB의 epoch(`pg:` + 32 hex). database oid·timeline·postmaster 시작 시각에서
  만든다. 복원(새 database·PITR)·재설치에 바뀌고, 제자리 파일 복원과 구분할 수 없어 단순 재시작에도 바뀐다(보수적).
  바뀌면 소비자는 cursor를 버리고 전량을 받는다.

## current·daily·forecasts export의 검증 완료 결과 재사용

forecasts(중기 제외)의 cursor 페이지도 상태 검사에 같은 앱 공용 승인 선택(최대60초)을 쓴다.
그 페이지의 승인/실패 반영은 최대60초 늦을 수 있다(소유자 결정 2026-11-11, current·daily와 같은
허용). 첫 페이지는 예전처럼 승인 선택을 새로 읽어 바로 반영하고 60초 상한을 걸지 않는다. 완성
페이지 재사용은 하지 않는다. 삭제 epoch token으로 head·plan 삭제 뒤에는 공용 선택을 다시 읽는다.

API만 승인 상태 조회 시작 뒤 최대60초의 완성 페이지를 재사용한다. 인증은 매 요청 수행하며 provider/limit/cursor/days/location와 설정·서명키 범위를 구분한다. x-export-read는 generated/reused다. generated_at은 wire 생성 시각이고 source_collected_at·snapshot_generated_at·as_of는 실제 검증한 데이터의 시각을 유지한다. 승인/실패·derived summary 반영은 이60초 한도 안에 지연될 수 있다.

cache hit는 snapshot TTL·manifest fingerprint·세대/plan/head와 실제 사용 파일 hash/count를 확인한다. 만료/재검산/갱신 실패를 이전 정상200/304로 연장하지 않는다. 주기적 전체 재생성은 없으며 요청이 있는 miss만 생성하고 같은 키는 합친다. 최초 미준비나 상한 밖 요청은 원 경로다. current의 긴 metadata 구성은 파일 guard 밖이고 새 pin의 파일 존재 검사와 DB commit만 GC와 직렬화한다. 참조 INSERT는512개 묶음으로 같은 TX에서 처리한다. SDK 기본 경로·SQL5초·파일 FD 검산은 유지한다. [근거와 실제 수용 단계](../reports/2026-11-08-aggressive-export-read.md).


0039의 API 완료 페이지 모드는 같은 RR의 삭제 epoch3행으로 불변 plan/세대/head 삭제 유무를 대조한다. 최초 전체 검산과 실제 사용 파일 hash/count는 유지한다. DELETE/TRUNCATE와 epoch 변경은 원자적이며 일반 수집 INSERT/UPDATE는 epoch를 쓰지 않는다. plan/세대 삭제는503·폐기, head 삭제는 재구성/stale, PG proof 미설치는503이다. 새로운 FK·원본 삭제·자동 backfill은 없다. worker 대기는 승인 조회 전이며60초는 실제 miss 읽기 시작부터 센다. 기존 캐시 hit나 이미 읽은 페이지의 시점은 다시 시작하지 않는다.
