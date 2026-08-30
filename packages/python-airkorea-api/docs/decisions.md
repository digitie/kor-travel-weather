# 아키텍처 결정 레코드 (ADR)

이 문서는 `python-airkorea-api` 프로젝트의 설계 방향, 핵심 설계 선택지 및 아키텍처 결정을 기록한다.

---

## ADR-1: Python 3.10+ 및 최소 런타임 의존성 정책

### 상태
승인됨 (2026-05-18)

### 배경
비공식 Python SDK로서 다양한 다운스트림(Django, FastAPI, CLI 유틸 등)에 통합되기 위해 범용성을 유지해야 하며, 과도한 외부 패키지 도입으로 인한 의존성 지옥을 방지해야 한다. 또한 Python 최신 문법(Structural Pattern Matching, Type Hinting 개선)을 사용하기 위해 기본 런타임 버전을 지정할 필요가 있다.

### 결정
1. 최저 지원 Python 버전을 `3.10`으로 고정한다.
2. 런타임 의존성을 `pydantic`, `pyproj`, `httpx` 3개로 엄격하게 한정한다. 비점오염원이나 냉매 등의 타 공공데이터 서비스는 이 저장소의 범위에서 제외한다.

### 근거
`pydantic`은 FastAPI 등 다운스트림 프레임워크와의 타입 호환성과 검증/직렬화 생태계를 그대로 물려받기 위해, `pyproj`는 WGS84 ⇄ AirKorea TM 좌표 변환을 직접 재구현하지 않고 검증된 CRS 라이브러리에 위임하기 위해, `httpx`는 동기/비동기 호출을 하나의 라이브러리로 지원하기 위해 선택했다(ADR-5 참고). `3.10` 하한은 `pyproject.toml`의 `requires-python`과 classifiers(`3.10`~`3.13`)에 그대로 반영되어 있다.

### 결과
`3.10` 지원을 유지하는 비용이 실제로 발생했다. `AirQualityGrade`(`IntEnum`) 값을 `from_code()`에 넘길 때 `3.11`부터 `IntEnum.__str__`이 plain int 포맷(`"3"`)으로 바뀐 반면 `3.10`은 이전 방식(`"AirQualityGrade.BAD"`)을 유지해, `int(str(value))` 파싱이 `3.10`에서만 조용히 `None`을 반환하는 버그가 있었다(2026-08-29, 커밋 `a2e01d0`에서 `isinstance(value, int)` 우선 분기로 수정). 이 저장소의 CI가 `3.10`~`3.13` 매트릭스를 실제로 돌리기 전까지는 이 버그가 발견되지 않았다.

---

## ADR-2: Pydantic 응답 모델링 및 raw 원본 페이로드 보존

### 상태
승인됨 (2026-05-18)

### 배경
공공데이터포털 AirKorea API는 사전에 예고 없이 응답 스키마 필드가 추가되거나 일부 결측 데이터로 인해 필드가 유실되는 불안정성이 있다. 이를 Pydantic으로 강력하게 밸리데이션할 시 예기치 못한 스키마 파싱 오류로 전체 서비스가 정지될 위험이 있다.

### 결정
1. API 응답 Pydantic 모델을 설계할 때 확인된 핵심 필드 위주로 타입 파싱을 엄격하게 수행한다.
2. 모든 응답 모델에는 `raw: dict[str, Any]` 필드를 보존하여, 스키마에 정의되지 않은 최신 필드도 다운스트림에서 우회 접근할 수 있도록 보장한다.

### 근거
공공데이터포털 API는 필드 추가/유실을 사전 공지 없이 반영하는 경우가 있어, 모든 필드를 엄격 검증하면 사소한 스키마 변경만으로도 파싱 전체가 실패할 위험이 있다. 확인된 필드만 타입화하고 원본을 함께 보존하면 신규/미확인 필드가 생겨도 다운스트림이 `raw`로 우회 접근할 수 있어 서비스 중단 위험을 낮춘다.

### 결과
`src/airkorea/models.py`의 모든 응답 모델(`StationMeasurement`, `SidoMeasurement` 등)이 `raw: RawRecord = Field(repr=False)` 필드를 갖는다. 모델은 `ConfigDict(frozen=True)`로 불변이며 `model_dump()`/`model_dump_json()`/`model_validate()`를 지원한다(CHANGELOG 0.4.0). 대가로 각 인스턴스가 파싱된 필드와 원본 dict를 이중으로 들고 있어 메모리 사용량이 늘어난다.

---

## ADR-3: WGS84(LatLon) 및 AirKorea TM(TmPoint) 좌표계 분리 및 변환

### 상태
승인됨 (2026-05-18)

### 배경
측정소 주소 및 정보에는 WGS84 위경도 좌표(`dmX`, `dmY`)가 들어있으나, 특정 근접 측정소 조회 API는 AirKorea 자체 중부원점 TM 좌표계(`tmX`, `tmY`)를 요구한다. 에이전트와 사용자가 X, Y 좌표축 순서나 위경도 입력 순서를 헷갈려 연산 오류를 내는 경우가 매우 빈번하다.

### 결정
1. WGS84 경위도 좌표는 반드시 `LatLon(lat, lon)` (위도, 경도) 객체로 다루고 튜플 입력도 이 순서로 강제한다.
2. AirKorea TM 평면 좌표는 반드시 `TmPoint(tm_x, tm_y)` (X축, Y축) 객체로 다루고 순서를 명확하게 캡슐화한다.
3. pyproj 라이브러리를 활용해 두 좌표계 간 변환을 `coords.py` 한 곳에서 정밀하게 책임진다.

### 근거
측정소 정보의 `dmX`/`dmY`(위경도 기반)와 근접 측정소 조회가 요구하는 TM 좌표(`tmX`/`tmY`)가 서로 다른 좌표계이면서 이름이 비슷해, 원시 tuple/float 인자만으로는 에이전트와 사용자 모두 축 순서와 좌표계를 혼동하기 쉽다. 값 객체로 감싸면 타입 자체가 좌표계를 드러내고, 순서 실수를 생성 시점에 방지할 수 있다.

### 결과
`src/airkorea/coords.py`에 `LatLon`, `TmPoint`와 `coerce_latlon()`, `coerce_tm_point()` 변환 helper가 있으며, `nearby_stations()`/`measurement_near()` 등은 값 객체·tuple·mapping·`lat=...`/`lon=...` 키워드 입력을 모두 받아 기존 문자열/튜플 호출과의 호환성을 유지한다(README "빠른 사용" 참고). 대가로 좌표를 다루는 모든 public 메서드가 다중 입력 형태를 정규화하는 코드를 유지해야 한다.

---

## ADR-4: HTTP 호출 에러 핸들링 및 resultCode 정규화 예외 매핑

### 상태
승인됨 (2026-05-18)

### 배경
AirKorea OpenAPI 서버는 요청 파라미터가 누락되거나 인증키가 만료된 상황에서도 HTTP 응답 코드로 200 OK를 반환하며, 에러 메시지를 JSON/XML payload 내부의 `resultCode`나 plain text 형태로 알려준다. 일반적인 HTTP 상태 코드 검사만으로는 오류 감지가 어렵다.

### 결정
1. HTTP 응답 수신 시 payload 내의 `resultCode` 필드를 추출 및 파싱하여 `00`이 아닐 경우 즉시 적절한 `AirKoreaResponseError` 및 서브클래스 에러로 예외를 발생시킨다.
2. Plain text 형태로 들어오는 에러 메시지도 감지하여 `AirKoreaParseError` 등으로 throw한다.

### 근거
HTTP 상태 코드만으로 성공/실패를 판단하면 인증키 만료나 파라미터 누락 같은 실패가 200 OK 뒤에 숨어 다운스트림이 빈 값이나 깨진 payload를 정상 응답으로 오인할 수 있다. `resultCode`를 파싱 단계에서 항상 검사해 typed exception으로 승격시키면 실패를 코드 레벨에서 강제로 드러낼 수 있다.

### 결과
`src/airkorea/_http.py`가 `response.header.resultCode`를 추출해 `00`이 아니면 `AirKoreaResponseError` 계열 예외로, JSON/plain text 파싱 자체가 실패하면 `AirKoreaParseError`로 변환한다(`response object is missing`, `response.header object is missing` 등 방어 분기 포함). 대부분의 서비스는 `serviceKey` 파라미터를 쓰지만 `UserSportSvc/getSvckeyDalyStats`만 `ServiceKey`를 요구하므로, 이 예외 매핑 계층은 파라미터명 예외도 함께 흡수한다.

---

## ADR-5: httpx Async / Sync 이중 클라이언트 인터페이스 제공

### 상태
승인됨 (2026-05-19)

### 배경
FastAPI 등 비동기 웹 프레임워크와 일반 배치성 동기 스크립트 모두에서 유연하게 사용될 수 있도록 클라이언트 인터페이스를 설계해야 한다.

### 결정
1. 비동기 HTTP 라이브러리인 `httpx`를 핵심 통신 도구로 사용한다.
2. 동기식 호출과 비동기식 호출을 둘 다 지원하기 위해, 공통 연산 로직을 설계하고 최종 게이트웨이 파사드에서 비동기(`AsyncHttpClient`) 및 동기(`HttpClient`)를 안전하게 감싸서 서비스한다.

### 근거
`httpx`가 동일 라이브러리 안에서 동기/비동기 클라이언트를 모두 제공하므로, 별도의 동기 전용 HTTP 라이브러리를 추가하지 않고도 두 호출 방식을 지원할 수 있다. FastAPI 같은 비동기 프레임워크에서는 이벤트 루프를 막지 않는 호출이 필요하고, 배치 스크립트 등에서는 단순 동기 호출이 더 다루기 쉽다.

### 결과
`src/airkorea/_http.py`에 `HttpClient`(동기)와 `AsyncHttpClient`(비동기)가 나란히 존재하고, `src/airkorea/client.py`의 `AirKoreaClient`가 동기 파사드, `AirKoreaClient.aio()`가 반환하는 `AsyncAirKoreaClient`가 비동기 파사드로 같은 endpoint 집합을 서비스한다. 대가로 HTTP 계층과 convenience 메서드를 동기/비동기 두 벌로 유지해야 한다.
