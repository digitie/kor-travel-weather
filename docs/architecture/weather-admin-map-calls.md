# 관리 지도 호출 줄이기(B6·B7·B8·B12)

`docs/reports/2026-11-24-map-call-performance-review.md` §5·§7 순서 8 중 관리 지도(경로 B)의
브라우저 호출 항목이다. 무실패화(B1·B2·B4·B5)는 [관리 지도 호출 무실패화](weather-admin-map-call-resilience.md),
공개 읽기 경로(B3·B9·B10·B11)는 [공개 읽기 경로](weather-public-read-path.md)를 따른다.

| 항목 | 바뀐 것 | 위치 |
| --- | --- | --- |
| B6 | 마커 실패를 영구 차단에서 시간 기반 재시도로 | `frontend/lib/weather-marker-queue.ts` |
| B7 | 지도 `load` 전 첫 카메라·컨테이너 크기로 화면 범위를 계산해 마커 요청을 바로 시작 | `frontend/lib/weather-viewport.ts`, `components/weather-map.tsx` |
| B8 | 마커 compact 응답 `GET /v1/weather/markers?view=compact`(opt-in) | API `routers/weather.py`, `frontend/lib/api.ts` |
| B12 | 위치 카탈로그 나머지 페이지 병렬 수신·화면 간 캐시·내용 ETag, 새로고침 중 마커 유지, 가까운 순 요청 | `frontend/lib/api.ts`, `lib/location-catalog.ts`, BFF `route.ts`, API `GET /v1/weather/locations` |

## B6. 마커 실패의 시간 기반 재시도

예전 큐는 묶음 요청이 실패하거나 응답에서 빠진 위치를 `failed`에 넣고 수동 "날씨 다시 조회" 전까지
다시 보내지 않았다. 일시 장애 한 번으로 그 위치가 화면에서 계속 비었다.

- `request()`가 502/503/504·네트워크 오류를 이미 두 번 다시 보낸다(B5). 큐의 실패는 그 뒤의 **최종
  실패**다.
- 최종 실패는 5초, 응답 누락(비활성·삭제 등으로 API가 돌려주지 않은 위치)은 60초부터 시도마다 두 배,
  상한 5분 뒤 다시 보낸다(`markerRetryDelay`). 대기 중에는 "일부 위치의 날씨를 불러오지 못했습니다.
  잠시 후 자동으로 다시 시도합니다."를 보이고 수동 버튼은 대기를 지운다.
- 대기가 끝나도 화면 범위 밖이면 보내지 않는다. 다시 화면에 들어오면 그때 보낸다. 큐는 화면에 남은
  실패 위치의 아직 오지 않은 가장 이른 재시도 시각에 timer 하나만 둔다. 동시 2묶음이 모두 진행 중이면
  timer를 두지 않고, 묶음이 끝날 때(`finally`) 재시도 시각이 지난 위치를 바로 보낸 뒤 다음 timer를
  잡는다(지난 시각으로 0ms timer를 반복하지 않는다). 필터 범위가 바뀌거나 화면을 떠나면 timer도
  지운다.
- 응답 누락은 즉시 반복하지 않는다(예전 "빈/부분 응답 자동 재요청 루프 금지"를 긴 대기로 유지).

## B7. 지도 load 전 화면 범위

MapLibre는 스타일과 첫 타일을 받은 뒤에야 `load`를 알리고, 그 전에는 `getBounds()`를 쓸 수 없다.
예전에는 `load` 전 화면 범위가 없어 마커 요청이 타일 뒤로 밀렸다.

- 지도 래퍼의 크기를 `ResizeObserver`로 읽고, 첫 카메라(`initialCamera`의 center·zoom)로 Web Mercator
  범위를 계산한다(`estimateViewport`, zoom 0 세계 512px로 MapLibre transform과 같은 식, 회전·기울기 없음).
- `load` 전에는 이 추정 범위로 마커 대상(`mapLocations`)을 고르고, `load` 뒤에는 실제 범위만 쓴다.
  `load` 뒤에는 크기를 관찰하지 않는다.
- 카탈로그가 늦게 와서 첫 선택 이동(`zoomOnFirstMoveRef`)으로 카메라가 바뀌면 그 뒤 실제 범위로 다시
  고른다. 이 경우 추정 범위로 받은 마커는 대표 마커 몇 곳뿐이다.

## B8. 마커 compact 응답

`GET /v1/weather/markers`에 `view=full|compact`(기본 `full`)를 더했다.

- `compact`는 `full`과 **같은 행**(위치당 상한·순서·특보 판정 동일)을 지도 표시 필드만 담는다.
  - 현재값·예보 행(`WeatherMarkerValueOut`): `provider`, `metric_key`, `metric_name`, `value_number`,
    `value_text`, `target_at`
  - 특보 행(`WeatherMarkerAlertOut`): `value_id`, `metric_name`, `value_text`, `severity`
  - `measurement_point`·`alert_scope_unverified`는 full과 같다.
- **기본 응답과 OpenAPI 200 schema는 바꾸지 않았다.** PinVi(`docs/integration-map.md`)는 `view` 없이
  부르므로 full 그대로이고, PinVi 계약 시험이 보는 `WeatherValueOut`·`MeasurementPointOut` 등도
  그대로다. compact 모양은 OpenAPI component `Envelope_list_WeatherMarkerCompactOut__`와 200 응답의
  `x-compact-schema`가 가리킨다(200을 union으로 바꾸면 생성 타입이 깨질 수 있어 피했다).
- gzip·직렬화는 B3에서 이미 worker thread에서 한다. 이 변경은 본문의 필드 수만 줄인다. 로컬 시험
  seed(2곳)에서 compact 본문은 full의 절반 미만이었다. 운영 크기는 측정하지 않았다.
- 관리 UI는 `getCompactMarkers()`만 쓴다. TS 타입은 `WeatherMarkerCompact`·`WeatherMarkerValue`·
  `WeatherMarkerAlert`(`frontend/lib/api.ts`)이고 상세 화면의 `WeatherValue`는 그 상위 타입이다.

## B12. 위치 카탈로그와 마커 갱신

### 나머지 페이지 병렬 수신

`fetchLocationCatalog`는 첫 페이지(1,000곳)의 `meta.page.total`로 나머지 페이지 수를 알고 동시 3개까지
받는다(`LOCATION_PAGE_CONCURRENCY`). 공개 읽기 worker를 혼자 차지하지 않도록 작게 두었다.

- total이 없으면 예전처럼 한 페이지씩 받는다.
- 받은 페이지들의 total이 서로 다르거나 행 수가 맞지 않으면(받는 사이 위치가 바뀜) 처음부터 한
  페이지씩 다시 받는다.
- 한 페이지가 최종 실패하면 진행 중인 다른 페이지를 끊고 실패를 돌려준다. 호출자 취소도 모든
  페이지에 전달된다.

### 화면·필터 사이 캐시와 내용 ETag

- 카탈로그는 TanStack Query(`AppQueryClientProvider`)에 `["weather-location-catalog", sourceKey]`로
  둔다. `sourceKey`는 지도 필터의 `JSON.stringify([제공처, 종류])`이고 날씨 화면 전체 목록은
  `[null, null]`이다. 신선한 60초(`CATALOG_STALE_MS`) 안의 재진입·필터 복귀는 다시 받지 않는다.
  요청 함수가 일시 오류를 이미 다시 보내므로 query 재시도는 끈다.
- 60초가 지나 다시 받을 때는 같은 key의 지난 카탈로그에서 페이지별 ETag를 `If-None-Match`로 보낸다.
  API `GET /v1/weather/locations`는 이 페이지의 `data`와 `limit`·`offset`·`total`로 만든 약한 ETag를
  주고, 맞으면 본문 없이 304다. 요청마다 바뀌는 meta(`request_id`·`generated_at`·`duration_ms`)는
  넣지 않는다. 304는 조회를 하므로 **DB 비용은 줄지 않고** 전송·JSON 파싱만 준다.
- 304 페이지는 가진 행을 그대로 쓰고, 모든 페이지가 304면 지난 카탈로그 객체를 그대로 돌려줘
  화면이 다시 계산하지 않는다.
- BFF(`app/api/weather/[...path]/route.ts`)는 브라우저의 `if-none-match`를 API로 보내고(기존),
  upstream `etag`를 브라우저로 넘긴다. 304·204는 본문 없이 전달한다. `cache-control`은 계속
  `no-store, private`라 브라우저 HTTP 캐시는 본문을 저장하지 않는다. ETag 보관은 앱 캐시가 한다.
- 대표 위치(`/v1/weather/regions`)도 같은 캐시를 쓴다. 이전처럼 실패·미준비는 전체 목록 경로로 넘어간다.
- 캐시에 목록이 있는 동안 뒤 재확인이 실패하면 목록을 지우지 않고 그대로 둔다.

### 새로고침 중 마커 유지와 요청 순서

- 마커 값은 필터 범위(제공처·종류)별로만 비운다. 상세의 "날씨 새로고침"(`refreshToken`)은 범위가
  같으므로 그린 마커를 남긴 채 새 세대로 다시 받아 도착한 위치부터 바꾼다.
- 새 세대 응답에서 빠진 위치(비활성·삭제 등으로 API가 돌려주지 않음)는 지난 값을 지운다. 오래된
  날씨를 새 값처럼 그리지 않기 위해서다. 묶음 요청이 최종 실패한 위치는 일시 장애일 수 있어 지난
  값을 남기고, "일부 위치의 날씨를 불러오지 못했습니다" 안내와 자동 재시도(B6)로 알린다.
- 같은 세대 안에서 지도↔목록을 오가면 이미 받은 위치는 다시 요청하지 않는다.
- 큐는 화면 중심에서 가까운 위치부터 묶음을 만든다(`markerFetchOrder`, 위도 cos를 곱한 평면 거리).
  상세에 연 선택 위치는 거리와 상관없이 맨 앞에 둬 첫 묶음으로 보낸다(화면 밖이거나 행정구역 대표가
  아니어도 상세 값이 늦게 채워지지 않게).

## 검증

- API: `packages/kor-travel-weather-api/tests/test_marker_compact_and_catalog_etag.py`(compact 행 동등성,
  기본 응답·OpenAPI 200 불변, ETag·304·total 변화), `test_public_read_path.py`의 직렬화 동등성 목록에 compact 추가.
- 브라우저: `components/weather-marker-budget.test.ts`(큐·재시도·순서·새로고침),
  `lib/location-catalog-pages.test.ts`(병렬·동시 상한·ETag·재수신·실패 전파),
  `lib/location-catalog.test.ts`(화면 간 캐시·304), `lib/weather-viewport.test.ts`, BFF `route.test.ts`.
- fixture e2e: `e2e/map-call-budget.spec.ts`(나머지 페이지 동시 수신, compact 마커 요청, 화면 이동 뒤 재수신 없음).

## 하지 않은 것

- 마커 compact에서 행 자체를 줄이는 것(아이콘·기온 지표만 남기기). `markerValues`가 현재값 유무로
  예보를 쓸지 고르므로, 행을 거르면 지금과 다른 상태를 보일 수 있다.
- 카탈로그 304의 DB 비용 절감(본문 읽기 전 비교). 위치 목록은 현재 행을 읽어야 ETag를 만들 수 있다.
- 운영 효과(첫 마커 표시 시각·전송량)는 측정하지 않았다.
