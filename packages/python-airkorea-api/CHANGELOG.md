# Changelog

## Unreleased

- `AirKoreaClient.call()` raw endpoint 호출과 `iter_pages()` 페이지 순회를 추가했습니다.
- `AirKoreaPage`, `AirKoreaCallContext`로 원본 item, page metadata, sanitized request context를 보존합니다.
- `sanitize_request_params()`, `make_cache_key()` 등 인증키 제거 metadata helper를 공개했습니다.
- PEP 561 `py.typed` marker가 배포 산출물에 포함되도록 package-data 설정을 명시했습니다.

## 0.4.0

- 응답 모델을 dataclass에서 Pydantic v2 `BaseModel` 기반으로 전환했습니다.
- 모든 응답 모델이 `model_dump()`, `model_dump_json()`, `model_validate()`를 지원합니다.
- 모델 불변성은 Pydantic `ConfigDict(frozen=True)`로 유지합니다.
- CLI JSON 직렬화가 Pydantic 모델을 인식하도록 보강했습니다.
- `pydantic>=2.7` 런타임 의존성을 추가했습니다.

## 0.3.0

- 외부 프로그램 연동을 위해 `DataTerm`, `InformCode`, `Pollutant`, `SidoName`, `StatsDataGubun`, `StatsSearchCondition`, `AirQualityGrade` enum을 추가했습니다.
- `LatLon`, `TmPoint`, `coerce_latlon`, `coerce_tm_point`, `resolve_airkorea_tm`으로 좌표 입력을 표준화했습니다.
- `nearby_stations()`와 `measurement_near()`가 좌표 값 객체, tuple, mapping 입력을 받을 수 있게 했습니다.
- 응답 모델에 `khai_grade_enum`, `item_code_enum`, `inform_code_enum`, `coordinates` property를 추가했습니다.
- 외부 라이브러리 사용 지침 문서 `docs/library-surface.md`를 추가했습니다.

## 0.2.0

- AirKorea 계열 OpenAPI 11개 서비스군 전체 목록을 `SUPPORTED_ENDPOINTS`로 문서화했습니다.
- 대기오염통계, 오존황사 발생정보, 미세먼지 경보, 사용자 지원, 고농도 PM2.5 예보, CAI, 국가배경농도 합성데이터, 영문 측정정보, 영문 측정소정보 API를 추가했습니다.
- 신규 모델 `AirQualityStat`, `AdvisoryOccurrence`, `DustAlarm`, `TrafficStat`, `HighPm25Forecast`, `CaiMeasurement`, `BackgroundConcentration`, `EnglishAirQualityMeasurement`, `EnglishStation`을 추가했습니다.
- `ServiceKey`처럼 서비스별 인증키 파라미터명이 다른 경우를 HTTP helper에서 지원합니다.
- 누락 방지 테스트와 반복 실수 방지 문서를 강화했습니다.

## 0.1.0

- 초기 패키지 구조를 작성했습니다.
- AirKorea 대기오염정보 API와 측정소정보 API의 핵심 메서드를 구현했습니다.
- HTTP/result-code 예외 매핑을 추가했습니다.
- WGS84와 AirKorea TM 좌표 변환 helper를 추가했습니다.
- README, API 명세, 테스트 원칙, 반복 실수 방지 문서를 작성했습니다.
