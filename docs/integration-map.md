# Integration map

| consumer | reads | owns | does not own |
| --- | --- | --- | --- |
| PinVi | public weather REST/OpenAPI (`/resolve`, `/markers`, `/latest`, `/forecast`, `/nearby`) | UI presentation | KMA credentials/ETL |
| ~~kor-travel-map~~ | 없음 — Map은 날씨를 읽지도 쓰지도 않는다(kor-travel-map `docs/adr/105-map-drops-weather-functionality.md`, accepted 2026-10-01). 날씨 소비자는 PinVi뿐이다 | — | — |
| Dagster | WeatherLocation catalog, KMA | source/fact publication, sync runs | consumer UI |
| admin UI | admin REST proxy | location enablement, run observation | direct DB/raw SQL |

Consumer는 `source_record_key`로 원본을 추적할 수 있지만 raw payload를 public
응답에서 직접 받지 않는다. 위치 lifecycle은 weather source가 소유하고, 지도
feature/POI identity는 map 프로젝트가 소유한다.

PinVi의 소비 표면과 호출 순서는 PinVi 저장소의 `docs/integrations/kor-travel-weather.md`
§2.1과 클라이언트 `apps/api/app/clients/kor_travel_weather.py`가 정본이다(여기 복제하지
않는다). 본 서비스 쪽 계약만 적으면, `GET /v1/weather/resolve`는 좌표 1회 해석이고 그 응답
하나에 `measurement_point`·`latest`·`forecast`·`alerts`가 담긴다
(`packages/kor-travel-weather-api/src/kortravelweather_api/routers/weather.py:145-155`
`ResolvedWeatherOut`) — 별도 호출 순서가 아니다. 이 프로젝트는 `/v1/features/*` feature
identity를 소유하지 않지만 weather alerts를 KMA `alerts` bundle로 소유한다.

## kor-travel-geo

본 서비스는 kor-travel-geo를 호출하지 않으며, geo 주소 데이터셋 릴리스(kor-travel-geo
`docs/adr/069-*.md` proposed, 미구현 — 응답 `dataset{version_token,data_token}` 도입 계획)는
본 서비스의 저장 데이터·공개 계약(`packages/kor-travel-weather-api/openapi.json`)에 영향을
주지 않는다. 따라서 본 서비스에 geo 릴리스 감지·재처리 경로를 두지 않는다.
`weather_locations.region_code`는 법정동·행정동 행정코드가 아니며 geo에서 유도되지 않는다.
본 서비스가 공급한 좌표·주소에서 다른 서비스가 행정코드를 유도한다면 그 갱신은 그쪽 소관이다
(Map은 weather 위치를 feature로 적재하지 않으므로 — Map ADR-105, 표 참조 — 그런 유도가
지금은 없고, 장래 생기더라도 Map 측 재지오코딩 경로(kor-travel-map `docs/adr/109-*.md`
proposed, 미구현)의 범위에서 다룰 일이다). 두 ADR 번호는 2026-10-13 설계에서 선점한 것이며
본 저장소 `docs/adr/`에는 없다(이 디렉터리는 Map 번호 체계의 부분 거울이다).

근거(worktree head 기준; 경로는 첫 언급만 저장소 루트 기준 전체 경로, 이후 괄호의 축약형):

- geo 미호출: geo HTTP base URL·API key env·클라이언트 모듈이 하나도 없다 — 저장소 전체
  `kor-travel-geo`/`geo` 언급 가운데 `http`·`_URL`·`_KEY`·import 형태의 hit는 0건이고,
  남는 언급은 전부 admin 로그인·세션 계약을 geo와 맞춘 주석(대표:
  `packages/kor-travel-weather-admin/frontend/lib/session.ts:235,329`, `.env.example:92`,
  `compose.yaml:432`)과 공용 Dagster 배선 주석(`compose.yaml:228`)이다. 배포 env
  계약(`compose.yaml` 각 service `environment`)에도 geo URL/키 env가 없다(geo hit는
  :228·:432 주석뿐).
- 위치 해석 경로: `weather_locations` Haversine 최근접 조회
  (`src/kortravelweather/repository.py:2777-2844`; `/v1/weather/resolve`는
  `packages/kor-travel-weather-api/src/kortravelweather_api/routers/weather.py:636-766`,
  이하 `routers/weather.py`), KMA 격자 변환
  (`packages/kor-travel-weather-dagster/src/kortravelweather_dagster/kma_weather.py:218-224`,
  이하 `kma_weather.py`), 그리고 외부 provider 자체의 위치 키 조회(AccuWeather geoposition,
  `src/kortravelweather/providers/external.py:807-817`, 이하 `providers/*.py`)가 전부다 —
  마지막 것도 provider API이지 geo가 아니다.
- `region_code`의 실체: `alembic/versions/0001_weather_source.py:22` `String(32)` 자유
  문자열이고, 0002~0018은 이 표의 열을 바꾸지 않는다(0007·0015가 FK로 참조할 뿐).
  provider가 적재한 anchor에서는 provider 주소·지역명 문자열 앞 32자다 — AirKorea
  `providers/airkorea.py:127`(측정소 주소), KHOA `providers/khoa.py:65,71`(도로명/지번 주소),
  KREX `providers/krex.py:133`(주소), KRForest `providers/krforest.py:75`(`local_area` 지역명),
  KRForest 청정넷 `providers/krforest_dust.py:80`(측정소 주소). admin이 만든 anchor에서는
  운영자가 입력한 임의 문자열이다(`routers/weather.py:277,290` `max_length=32`; KMA 경보
  매칭용 구역 식별자 용도, `kma_weather.py:515-523` docstring).

간접 영향 메모 — 원인은 geo 릴리스가 아니라 provider 문자열·행정구역 개편이며, geo는
이를 알려 주지 않는다(백로그화 여부는 소유자 결정 대기, 2026-10-13 — 그 전까지는 메모만):

- `_SIDO_ALIASES`(`providers/airkorea.py:25-63`) 하드코딩 시도 표는 행정구역 개편 시
  사람이 갱신한다.
- AirKorea 측정소 `location_id`는 측정소 코드가 없을 때 provider 주소 문자열을 포함한
  해시다(`providers/airkorea.py:104-107`) — 도로명 개편으로 provider 문자열이 바뀌면
  재키된다(provider 주도).
- KMA 경보 fan-out `_warning_matches_target`은 `region_code` 부분문자열 비교다
  (`kma_weather.py:515-545`) — 행정구역 명칭 개편이 provider 문자열에 반영되면 매칭이
  어긋날 수 있다. 기존 하드닝 항목 "지역별 발령기관 매핑"(`docs/decisions.md:12-15`)과
  같은 축이다.
