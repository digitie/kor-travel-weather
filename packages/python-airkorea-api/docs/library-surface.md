# 라이브러리 표면 설계

외부 프로그램에서 `airkorea`를 안정적으로 사용하기 위한 public 타입과 좌표 규칙입니다.

## 클라이언트 형태

클라이언트 facade는 `python-krheritage-api`와 같은 형태를 따릅니다. `AirKoreaClient()`는 동기 클라이언트이고, `AirKoreaClient.aio()`는 같은 public method를 `await`로 호출하는 `AsyncAirKoreaClient`를 반환합니다.

```python
from airkorea import AirKoreaClient

air = AirKoreaClient()
rows = air.station_measurements("종로구", num_of_rows=1)

async with AirKoreaClient.aio() as async_air:
    async_rows = await async_air.station_measurements("종로구", num_of_rows=1)
```

서비스키를 명시할 때는 키워드 인자를 사용합니다.

```python
air = AirKoreaClient(service_key="decoded-service-key")
async_air = AirKoreaClient.aio(service_key="decoded-service-key")
```

## 문자열 호환 enum

기존 문자열 기반 코드는 계속 동작합니다. 새 코드에서는 enum 사용을 권장합니다.

| enum | 용도 | 예시 |
|---|---|---|
| `DataTerm` | 측정소 실시간 측정정보 조회 기간 | `DataTerm.DAILY` |
| `InformCode` | 예보 통보 코드 | `InformCode.PM10` |
| `Pollutant` | 오염물질/item code | `Pollutant.PM25` |
| `SidoName` | 시도명 | `SidoName.SEOUL` |
| `StatsDataGubun` | 통계 집계 단위 | `StatsDataGubun.HOUR` |
| `StatsSearchCondition` | 통계 조회 조건 | `StatsSearchCondition.WEEK` |
| `AirQualityGrade` | 대기질 등급 | `AirQualityGrade.MODERATE` |

```python
from airkorea import AirKoreaClient, Pollutant, SidoName

air = AirKoreaClient()
rows = air.sido_measurements(SidoName.SEOUL)
alarms = air.dust_alarms(2026, item_code=Pollutant.PM25)
```

## 좌표 표준

위경도 public 경계는 이 라이브러리의 `LatLon`을 기준으로 합니다.
`LatLon`은 WGS84 public DTO에서 `lat, lon` 순서를 쓰고, AirKorea TM 좌표는
`tm_x, tm_y` 순서입니다. GeoJSON/WKT/GIS 출력 경계에서만 해당 표준의 `lon, lat` 순서로 변환합니다.

| 타입 | 의미 | 순서 |
|---|---|---|
| `LatLon` | WGS84 위경도 public DTO | `lat, lon` |
| `TmPoint` | AirKorea TM 좌표 | `tm_x, tm_y` |

권장:

```python
from airkorea import LatLon

point = LatLon(lat=37.5665, lon=126.9780)
nearby = air.nearby_stations(coordinate=point)
```

다른 입력:

```python
from airkorea import LatLon

air.nearby_stations(lat=37.5665, lon=126.9780)
air.nearby_stations(coordinate=(37.5665, 126.9780))
air.nearby_stations(coordinate=LatLon(37.5665, 126.9780))
air.nearby_stations(coordinate={"latitude": 37.5665, "longitude": 126.9780})
air.nearby_stations(tm={"tmX": 198242, "tmY": 451580})
```

`coordinate`와 `tm` 계열 입력을 동시에 넘기면 `ValueError`가 발생합니다. 좌표계가 섞였을 때 조용히 틀린 측정소를 고르는 일을 막기 위한 의도적인 동작입니다.

## Pydantic 응답 모델

응답 객체는 Pydantic v2 `BaseModel` 기반입니다. 필드 접근은 속성으로 하고, 외부 직렬화에는 `model_dump()`나 `model_dump_json()`을 사용할 수 있습니다. 원본 응답은 `raw`에 보존합니다.

```python
latest = air.latest_station_measurement("종로구")
if latest is not None:
    print(latest.khai_grade_enum)
    print(latest.khai_grade_enum.label if latest.khai_grade_enum else None)
    print(latest.model_dump(mode="json"))

station = air.stations(station_name="종로구")[0]
print(station.coordinates)
```

대표 property:

- `AirQualityMeasurement.khai_grade_enum`
- `CaiMeasurement.khai_grade_enum`
- `EnglishAirQualityMeasurement.khai_grade_enum`
- `ForecastNotice.inform_code_enum`
- `AirQualityStat.item_code_enum`
- `DustAlarm.item_code_enum`
- `Station.coordinates`
- `EnglishStation.coordinates`

## Raw page와 페이지 순회

일반적인 사용은 typed convenience method(`stations()`, `sido_measurements()` 등)를 권장합니다. 아직 typed model이 충분하지 않거나, 응답의 `pageNo`/`numOfRows`/`totalCount` 같은 원본 페이지 메타데이터가 필요하면 `AirKoreaClient.call()`을 사용합니다.

```python
page = air.call(
    "MsrstnInfoInqireSvc",
    "getMsrstnList",
    {"addr": "서울"},
    num_of_rows=10,
)

page.items          # tuple[RawRecord, ...]
page.raw            # response.body 원본 mapping
page.has_next_page  # pageNo/numOfRows/totalCount 기반
page.next_page_no   # 다음 pageNo 또는 None
page.context        # AirKoreaCallContext
```

`call()`은 `SUPPORTED_ENDPOINTS`에 등록된 서비스/endpoint만 호출합니다. 서비스명은 대소문자 차이를 허용하지만 endpoint 철자는 공식 철자를 그대로 요구합니다. 특히 `getCtprvnMesureLIst`, `getCtprvnMesureSidoLIst`, `getMsrstnAcctoRMmrg`는 문서 철자를 유지합니다.

여러 페이지를 가져올 때는 `iter_pages()`를 사용합니다.

```python
for page in air.iter_pages(
    "MsrstnInfoInqireSvc",
    "getMsrstnList",
    {"addr": "서울"},
    num_of_rows=100,
    max_pages=5,
):
    for row in page.items:
        print(row["stationName"])
```

모듈 수준 helper도 공개 API입니다.

- `has_next_page(body)`
- `next_page_no(body)`
- `iter_paginated_pages(fetch_page, ...)`

## Call context와 캐시 키

`AirKoreaCallContext.request_params`는 `serviceKey`, `ServiceKey`, `api_key`, `auth_key` 같은 인증키성 파라미터를 저장하지 않습니다. 로그, 캐시 키, fixture metadata에 그대로 써도 키가 남지 않도록 보수적으로 제거합니다.

```python
from airkorea import make_cache_key, sanitize_request_params

safe_params = sanitize_request_params({"serviceKey": "secret", "addr": "서울"})
cache_key = make_cache_key("getMsrstnList", safe_params, service_name="MsrstnInfoInqireSvc")
```

## 서비스키 로딩

`AirKoreaClient()`와 `AirKoreaClient.aio()`는 `service_key`를 생략하면 기본적으로 `DATA_GO_KR_SERVICE_KEY` 환경변수를 읽고, 값이 없으면 현재 작업 디렉터리의 `.env` 파일에서 같은 이름을 찾습니다. 외부 UI에서 `.env` 경로를 직접 지정해야 할 때는 `from_env()`를 사용할 수 있습니다.

```python
from airkorea import AirKoreaClient

air = AirKoreaClient()
air_from_file = AirKoreaClient.from_env(dotenv_path="local.env")
async_air = AirKoreaClient.aio()
```

서비스키는 클라이언트 내부에서 `normalize_service_key()`를 거치므로, 복사/붙여넣기 중 섞인 앞뒤 공백, 줄바꿈, 탭은 제거됩니다.

## API 카탈로그

`api_catalog()`는 지원 endpoint 전체를 사람이 읽기 쉬운 데이터셋명과 함께 반환합니다. Streamlit에서는 `api_catalog_dicts()`를 `st.dataframe()`에 바로 넘길 수 있습니다.

```python
from airkorea import api_catalog_dicts, api_catalog_for_method

rows = api_catalog_dicts()
selected = api_catalog_for_method("station_measurements")
```

카탈로그 항목 주요 필드:

- `dataset_name`: 공공데이터포털 데이터셋명
- `service_label`: 짧은 서비스군 이름
- `service_name`: AirKorea 서비스명
- `endpoint`: 공식 endpoint 철자
- `method_names`: 연결된 Python public method 이름 목록
- `service_key_param`: `serviceKey` 또는 `ServiceKey`
- `service_key_url`: 서비스키 신청/발급에 사용할 공공데이터포털 링크
- `portal_url`: 데이터셋 상세 링크

## 디버그 fixture helper

외부 디버그 Web UI나 수동 조사 스크립트는 `run_debug_method()`로 public method 실행 결과를 `DebugRun`으로 받을 수 있습니다. `DebugRun`에는 입력, 인증키 제거 요청, raw response body, typed 결과, trace, error가 들어갑니다.

```python
from airkorea import AirKoreaClient, run_debug_method, save_debug_fixture

air = AirKoreaClient()
debug_run = run_debug_method(
    air,
    "station_measurements",
    {"station_name": "종로구", "num_of_rows": 1},
)

save_debug_fixture(
    base_dir="tests/fixtures",
    case_name="jongno_normal",
    debug_run=debug_run,
)
```

`DebugRun.catalog`는 선택된 함수와 연결된 카탈로그 dict 목록입니다. Streamlit의 Debug Trace 탭에서는 다음처럼 실제 데이터셋명과 서비스키 링크를 표시할 수 있습니다.

```python
st.dataframe(debug_run.catalog)
st.code("\n".join(debug_run.trace))
```

관련 public helper:

- `ApiCatalogEntry`
- `DebugRun`
- `api_catalog()`
- `api_catalog_dicts()`
- `api_catalog_for_method(method_name)`
- `api_catalog_for_debug_input(function_name, input_data)`
- `get_api_catalog_entry(service_name, endpoint)`
- `run_debug_method(client, function_name, input_data)`
- `save_debug_fixture(...)`
- `load_debug_fixture(path)`
- `jsonable(value)`
- `redact_sensitive(value)`
- `slugify(value)`

fixture 형식과 replay runner 구조는 `docs/debug-fixtures.md`를 따릅니다.

## 정규화 helper

외부 프로그램에서 사용자 입력을 먼저 검증하고 싶다면 helper를 직접 사용할 수 있습니다.

```python
from airkorea.codes import normalize_pollutant_code, validate_sido_name
from airkorea.coords import coerce_latlon

item_code = normalize_pollutant_code("pm2.5")
sido_name = validate_sido_name("서울")
point = coerce_latlon({"lat": "37.5665", "lon": "126.9780"})
```
