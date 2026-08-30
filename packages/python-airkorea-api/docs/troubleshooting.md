# 문제 해결

## 인증 오류

증상:

- `AirKoreaAuthError`
- `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`
- HTTP 401/403

점검:

- 공공데이터포털에서 해당 OpenAPI 활용신청이 승인됐는지 확인한다.
- Encoding 키와 Decoding 키를 모두 시도한다.
- `DATA_GO_KR_SERVICE_KEY`에 공백이나 따옴표가 섞이지 않았는지 확인한다.
- 환경변수가 없다면 현재 작업 디렉터리의 `.env`에 `DATA_GO_KR_SERVICE_KEY=...`가 있는지 확인한다.
- 복사/붙여넣기로 섞인 앞뒤 공백, 줄바꿈, 탭은 클라이언트가 제거하지만, 키 중간에 잘못 들어간 문자는 포털에서 다시 복사한다.
- API별 활용신청 링크는 `api_catalog_dicts()`의 `service_key_url` 또는 `DebugRun.catalog`에서 확인한다.
- `UserSportSvc/getSvckeyDalyStats`는 `ServiceKey` 대문자 파라미터를 사용한다.

## 데이터 없음

증상:

- `AirKoreaNoDataError`
- 빈 list

점검:

- 조회 날짜, 측정소명, 시도명, pollutant code가 실제로 존재하는지 확인한다.
- 실시간 API는 측정소 상태에 따라 특정 시간대 데이터가 없을 수 있다.
- 경보/주의보 API는 해당 기간에 발령 이력이 없으면 비어 있을 수 있다.

## 파싱 오류

증상:

- `AirKoreaParseError`

점검:

- 포털이 JSON 대신 XML/HTML 오류를 반환했는지 확인한다.
- 신규 필드명이 확인되면 parser와 fixture 테스트를 함께 추가한다.
- `raw` 필드에 원본 응답이 보존되므로 먼저 원본 키를 확인한다.

## 좌표가 이상함

점검:

- `stations()`의 `dmX`, `dmY`는 위도/경도로 노출된다.
- `nearby_stations()`는 TM 좌표를 요구한다.
- 위도/경도만 있다면 `nearby_stations(lat=..., lon=...)`를 사용한다.
- 이미 AirKorea TM 좌표가 있다면 `nearby_stations(tm_x=..., tm_y=..., ver="1")`처럼 직접 넘긴다.

## Endpoint 404

점검:

- `getCtprvnMesureLIst`와 `getCtprvnMesureSidoLIst`의 `LIst` 대소문자를 바꾸지 않는다.
- 월평균은 `getMsrstnAcctoRMmrg`다.
- 영문 서비스는 `atmstMsrstnRltmInfo/getList`, `atmstMsrstnInfoEngNm/getList`다.
- `airkorea.client.SUPPORTED_ENDPOINTS`와 `airkorea-api.md`를 먼저 확인한다.
