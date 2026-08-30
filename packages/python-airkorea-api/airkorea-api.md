# AirKorea OpenAPI 구현 명세

이 문서는 `airkorea`가 지원하는 공식 AirKorea OpenAPI 목록과 Python 메서드 매핑입니다. 확인 기준일은 2026-04-30입니다.

## 공식 출처

| 데이터 | 공공데이터포털 |
|---|---|
| 한국환경공단_에어코리아_대기오염정보 | https://www.data.go.kr/data/15073861/openapi.do |
| 한국환경공단_에어코리아_측정소정보 | https://www.data.go.kr/data/15073877/openapi.do |
| 한국환경공단_에어코리아_대기오염통계 현황 | https://www.data.go.kr/data/15073855/openapi.do |
| 한국환경공단_에어코리아_오존황사 발생정보 | https://www.data.go.kr/data/15073872/openapi.do |
| 한국환경공단_에어코리아_미세먼지 경보 발령 현황 | https://www.data.go.kr/data/15073885/openapi.do |
| 한국환경공단_에어코리아_사용자 지원 | https://www.data.go.kr/data/15075624/openapi.do |
| 한국환경공단_에어코리아_고농도 초미세먼지(50초과) 예보 정보 | https://www.data.go.kr/data/15112307/openapi.do |
| 한국환경공단_에어코리아_통합대기환경지수(CAI) 조회 서비스 | https://www.data.go.kr/data/15140253/openapi.do |
| 한국환경공단_대기오염물질 국가배경농도 합성데이터 조회 서비스 | https://www.data.go.kr/data/15153593/openapi.do |
| 한국환경공단_에어코리아 측정소별 실시간 측정정보 영문 조회 서비스 | https://www.data.go.kr/data/15156659/openapi.do |
| 한국환경공단_에어코리아_측정소 영문 정보 조회 서비스 | https://www.data.go.kr/data/15156708/openapi.do |

## 구현 목록

| 서비스 | Base URL | Endpoint | Python 메서드 |
|---|---|---|---|
| 대기오염정보 | `.../ArpltnInforInqireSvc` | `getMsrstnAcctoRltmMesureDnsty` | `station_measurements`, `latest_station_measurement` |
| 대기오염정보 | `.../ArpltnInforInqireSvc` | `getCtprvnRltmMesureDnsty` | `sido_measurements` |
| 대기오염정보 | `.../ArpltnInforInqireSvc` | `getUnityAirEnvrnIdexSnstiveAboveMsrstnList` | `unhealthy_stations` |
| 대기오염정보 | `.../ArpltnInforInqireSvc` | `getMinuDustFrcstDspth` | `forecast_notices` |
| 대기오염정보 | `.../ArpltnInforInqireSvc` | `getMinuDustWeekFrcstDspth` | `weekly_forecasts` |
| 측정소정보 | `.../MsrstnInfoInqireSvc` | `getMsrstnList` | `stations` |
| 측정소정보 | `.../MsrstnInfoInqireSvc` | `getNearbyMsrstnList` | `nearby_stations`, `measurement_near` |
| 측정소정보 | `.../MsrstnInfoInqireSvc` | `getTMStdrCrdnt` | `tm_coordinates` |
| 대기오염통계 현황 | `.../ArpltnStatsSvc` | `getCtprvnMesureLIst` | `sido_average_stats` |
| 대기오염통계 현황 | `.../ArpltnStatsSvc` | `getCtprvnMesureSidoLIst` | `city_average_stats` |
| 대기오염통계 현황 | `.../ArpltnStatsSvc` | `getMsrstnAcctoRDyrg` | `station_daily_stats` |
| 대기오염통계 현황 | `.../ArpltnStatsSvc` | `getMsrstnAcctoRMmrg` | `station_monthly_stats` |
| 오존황사 발생정보 | `.../OzYlwsndOccrrncInforInqireSvc` | `getOzAdvsryOccrrncInfo` | `ozone_advisories` |
| 오존황사 발생정보 | `.../OzYlwsndOccrrncInforInqireSvc` | `getYlwsndAdvsryOccrrncInfo` | `yellow_dust_advisories` |
| 미세먼지 경보 발령 현황 | `.../UlfptcaAlarmInqireSvc` | `getUlfptcaAlarmInfo` | `dust_alarms` |
| 사용자 지원 | `.../UserSportSvc` | `getSvckeyDalyStats` | `traffic_stats` |
| 고농도 PM2.5 예보 | `.../MinuDustFrcstDspthSvc` | `getMinuDustFrcstDspth50Over` | `high_pm25_forecasts` |
| CAI 조회 | `.../RltmKhaiInfoSvc` | `getMsrstnKhaiRltmDnsty` | `cai_measurements` |
| 국가배경농도 합성데이터 | `.../arpltsNtnBkgrDnstSyrdt` | `getList` | `background_concentrations` |
| 영문 실시간 측정정보 | `.../atmstMsrstnRltmInfo` | `getList` | `english_measurements` |
| 측정소 영문 정보 | `.../atmstMsrstnInfoEngNm` | `getList` | `english_stations` |

코드에는 같은 내용이 `airkorea.client.SUPPORTED_ENDPOINTS`로 고정되어 있습니다. 목록을 추가하거나 수정하면 테스트도 함께 수정해야 합니다.

## 코드 카탈로그

외부 UI나 디버그 도구에서 API 목록을 보여줄 때는 `airkorea.api_catalog()` 또는 `airkorea.api_catalog_dicts()`를 사용합니다. 카탈로그는 `SUPPORTED_ENDPOINTS`와 같은 endpoint 집합을 대상으로 하며, 각 항목은 다음 정보를 포함합니다.

- `dataset_name`: 공공데이터포털 데이터셋명
- `service_label`: 짧은 서비스군 이름
- `service_name`: AirKorea 서비스명
- `endpoint`: 공식 endpoint 철자
- `method_names`: 연결된 Python public method
- `service_key_param`: `serviceKey` 또는 `ServiceKey`
- `service_key_url`: 서비스키 신청/발급에 사용할 공공데이터포털 링크
- `portal_url`: 데이터셋 상세 링크

Streamlit 디버그 UI는 API 선택 후 `service_key_url`을 링크로 보여주면 됩니다. `run_debug_method()`가 반환하는 `DebugRun.catalog`에도 선택된 함수와 연결된 카탈로그 항목이 들어갑니다.

## 공통 호출 규칙

대부분의 서비스는 다음 공통 파라미터를 사용합니다.

| 파라미터 | 처리 |
|---|---|
| `serviceKey` | 공공데이터포털 인증키 |
| `returnType` | `json`으로 고정 |
| `pageNo` | 페이지 번호 |
| `numOfRows` | 한 페이지 결과 수 |

예외:

- `UserSportSvc/getSvckeyDalyStats`는 공식 명세가 `ServiceKey`를 사용하므로 대문자 `ServiceKey`로 보냅니다.
- `ArpltnStatsSvc/getCtprvnMesureLIst`의 `LIst`는 오타가 아니라 공식 endpoint 철자입니다.
- `ArpltnStatsSvc/getMsrstnAcctoRMmrg`는 월평균 endpoint입니다. `getMsrstnAcctoRmmrg`는 404가 납니다.
- 2025년에 추가된 영문 서비스는 기존 `...EngSvc`가 아니라 `atmstMsrstnRltmInfo/getList`, `atmstMsrstnInfoEngNm/getList`입니다.

## 응답 처리

공통 JSON envelope:

```json
{
  "response": {
    "header": {
      "resultCode": "00",
      "resultMsg": "NORMAL_CODE"
    },
    "body": {
      "items": []
    }
  }
}
```

AirKorea 응답은 서비스와 시점에 따라 `body.items`가 list, dict, `items.item` 중 하나로 올 수 있습니다. `airkorea`는 세 형태를 모두 list로 정규화합니다.

## 모델링 원칙

- 확인된 주요 필드는 Pydantic 모델 필드로 변환합니다.
- 신규 API처럼 상세 응답 스키마가 포털 HTML에 충분히 드러나지 않는 경우 `raw`를 항상 보존합니다.
- `-`, 빈 문자열, `null`, `none`, `nan`은 `None`으로 처리합니다.
- 숫자 문자열은 `float` 또는 `int`로 변환합니다.
- 실시간 측정 시각은 KST aware `datetime`으로 변환합니다.

## 타입/좌표 표준화

- 문자열 입력은 계속 지원합니다.
- 새 코드에서는 `DataTerm`, `InformCode`, `Pollutant`, `SidoName`, `StatsDataGubun`, `StatsSearchCondition` enum 사용을 권장합니다.
- WGS84 좌표 public 경계는 이 라이브러리의 `LatLon(lat, lon)`으로 표준화합니다.
- AirKorea TM 좌표는 `TmPoint(tm_x, tm_y)`로 표준화합니다.
- `nearby_stations()`와 `measurement_near()`는 `LatLon`, `(lat, lon)`, mapping, `lat=...`, `lon=...` 입력을 받습니다.
- 응답 모델은 `khai_grade_enum`, `item_code_enum`, `inform_code_enum`, `coordinates` 같은 후처리용 property를 제공합니다.

## 범위 밖

이 프로젝트는 AirKorea 대기질 API 전용입니다. 한국환경공단이 같은 제공기관 코드로 제공하는 비점오염원, 재활용, 냉매 등 비-AirKorea 서비스는 포함하지 않습니다.
