# 반복 실수 방지 로그

이 문서는 한 번 겪은 실수를 다시 반복하지 않기 위한 작업 메모입니다. 관련 코드가 바뀌면 테스트와 이 문서를 같이 갱신합니다.

## 1. AirKorea API를 2개 서비스군으로 착각하지 말 것

- 증상: 대기오염정보와 측정소정보만 구현하고 통계/경보/영문/합성데이터를 놓침
- 원인: 공공데이터포털 검색 결과가 서비스별로 흩어져 있고, 일부 페이지는 상세기능 선택 UI 때문에 HTML에 첫 endpoint만 보임
- 규칙: `airkorea.client.SUPPORTED_ENDPOINTS`를 기준 목록으로 유지하고 `tests/test_expanded_api.py`에서 서비스군 수와 핵심 endpoint를 고정 assert한다

## 2. Endpoint 대소문자와 오타처럼 보이는 철자를 고치지 말 것

- `getCtprvnMesureLIst`의 `LIst`는 공식 철자다
- `getCtprvnMesureSidoLIst`도 같은 패턴이다
- 월평균 통계는 `getMsrstnAcctoRMmrg`다
- `getMsrstnAcctoRmmrg`는 404가 난다

## 3. 영문 서비스 URL을 예전 패턴으로 추측하지 말 것

- 잘못된 추측: `ArpltnInforInqireEngSvc`, `msrstnInfoInqireEngSvc`
- 현재 구현: `atmstMsrstnRltmInfo/getList`, `atmstMsrstnInfoEngNm/getList`
- 규칙: 신규 영문 API는 base URL이 기존 한글 API와 다르므로 `SUPPORTED_ENDPOINTS`와 `airkorea-api.md`를 먼저 확인한다

## 4. `serviceKey`만 있다고 가정하지 말 것

- 대부분은 `serviceKey`를 쓴다
- 사용자 지원 `UserSportSvc/getSvckeyDalyStats`는 공식 명세가 `ServiceKey`다
- 규칙: HTTP helper는 `service_key_param`을 받을 수 있어야 하고, 테스트에서 `ServiceKey` 예외를 검증한다

## 5. `body.items.item`만 있다고 가정하지 말 것

- AirKorea JSON은 `body.items`가 list로 바로 올 수 있다
- 단일 dict로 오거나 `items.item`으로 감싸질 수도 있다
- 규칙: `_items()`에서 list, dict, `items.item`을 모두 정규화한다

## 6. `dmX`, `dmY`를 평면좌표 X/Y로 오해하지 말 것

- 측정소정보의 `dmX`, `dmY`는 실제 샘플 기준 위도/경도에 해당한다
- 모델에서는 `Station.lat = dmX`, `Station.lon = dmY`로 노출한다
- 근접 측정소 API는 별도 TM 좌표를 요구하므로 lat/lon 입력 시 `wgs84_to_tm()`으로 변환한다

## 7. 결측값을 0으로 바꾸지 말 것

- AirKorea는 결측을 `-`, 빈 문자열, `null` 등으로 줄 수 있다
- `0`은 실제 측정값일 수 있으므로 결측은 항상 `None`으로 보존한다

## 8. 신규 API 응답 스키마를 과하게 단정하지 말 것

- 일부 포털 페이지는 전체 응답 필드를 정적 HTML로 충분히 노출하지 않는다
- 규칙: 확인된 필드만 Pydantic 모델 필드로 파싱하고 원본 `raw`를 항상 보존한다
- 필드명이 추가로 확인되면 parser를 확장하고 fixture 테스트를 추가한다

## 9. 실시간 데이터를 고정값 테스트로 만들지 말 것

- 실시간 측정값, 예보 문구, 경보 상태는 시간에 따라 바뀐다
- 기본 테스트는 fixture/mock 기반으로 둔다
- live 테스트는 integration marker로 분리하고 값 자체보다 envelope, 타입, 필드 존재를 확인한다

## 10. enum을 추가하면서 문자열 호환성을 깨지 말 것

- 외부 프로그램은 이미 `"PM10"`, `"서울"`, `"DAILY"` 같은 문자열을 넘길 수 있다
- enum은 새 코드의 타입 안정성을 위한 추가 표면이지 기존 문자열 API의 대체가 아니다
- 규칙: public method는 enum과 문자열을 모두 받아야 하고 normalize helper에서 최종 API 문자열로 변환한다

## 11. 위경도 순서를 섞지 말 것

- WGS84는 항상 `lat, lon` 순서다
- AirKorea TM은 항상 `tm_x, tm_y` 순서다
- public 좌표 경계에는 이 라이브러리의 `LatLon(lat=..., lon=...)`을 사용한다
- 규칙: `LatLon` 공개 DTO는 `(lat, lon)`, AirKorea TM은 `(tm_x, tm_y)`, GeoJSON/WKT/GIS 출력은 해당 표준의 `(lon, lat)`로 분리한다

## 12. 로컬 도구/인코딩 문제를 코드 문제로 착각하지 말 것

- 증상: `rg` 실행이 `Access is denied`로 실패하거나, PowerShell에서 한글 문서가 깨져 보임
- 원인: 현재 Windows 작업 환경의 실행 권한/출력 인코딩 문제일 수 있음
- 규칙: `rg`가 막히면 재시도에 시간을 쓰지 말고 `git ls-files`, `Get-ChildItem`, `Select-String`으로 파일 목록과 검색을 처리한다
- 규칙: 한글 문서를 읽을 때는 `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Get-Content -Encoding UTF8 -Raw <path>`처럼 UTF-8을 명시한다
- 규칙: 깨져 보이는 문서를 그대로 수정하지 말고, 인코딩을 명시해서 정상 표시를 확인한 뒤 편집한다
