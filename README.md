# kor-travel-weather

대한민국 전역의 관측·예보·기상특보를 모아 제공하는 독립 날씨 데이터 소스다.
`kor-travel-map`의 데이터 계약·운영 패턴을 가져오되, 장소/축제/가격 같은 지도
도메인은 소유하지 않는다. PinVi와 `kor-travel-map`은 저장소를 직접 읽지 않고
REST/OpenAPI를 통해 날씨 데이터를 소비한다.

## 목표

- `python-kma-api`를 통해 기상청 초단기실황·초단기예보·단기예보·중기예보·특보를
  매시간 수집한다.
- `python-airkorea-api`로 측정소 카탈로그와 대기질 관측을 매시간 갱신하고, AirKorea
  측정소 좌표를 외부 weather provider의 공통 anchor로 사용한다.
- 위치와 원천 시각을 보존하는 정규화된 weather fact를 멱등적으로 저장한다.
- 공용 REST API로 최신값·예보 timeline·좌표 기반 근접 위치를 제공한다.
- Dagster의 KMA·AirKorea·외부 provider hourly schedule과 운영 이력을 admin UI에서 확인하고 위치를 관리한다.
- 특정 소비자에 종속되지 않는 provider/dataset/metric 계약과 raw payload 추적성을 유지한다.

## 운영 모델

```text
KMA/external providers ──> Dagster assets ──> PostgreSQL ──> FastAPI/OpenAPI
                                           │                  │
                                           └──────────────> Next.js admin UI
```

API와 Dagster는 같은 저장소를 사용하지만 별도 패키지다. 외부 소비자는
`packages/kor-travel-weather-api/openapi.json`에 해당하는 FastAPI 계약만 사용한다.
provider client 위에 불필요한 wrapper를 만들지 않고, Dagster resource가
`kma.KmaClient`를 직접 생성한다. KMA의 격자 변환과 발표 시각 계산도
`python-kma-api`의 `kma.grid`/`kma.time_utils`를 그대로 사용한다.

## 빠른 시작

```bash
cd /mnt/f/dev/kor-travel-weather
uv sync --extra dev --extra dagster
cp .env.example .env
set -a; . .env; set +a
export PGPASSWORD="$POSTGRES_PASSWORD"

# PostgreSQL만 지원한다. Compose DB를 먼저 기동한다(호스트 포트 14100).
docker compose up -d db
# 로컬 PostgreSQL schema + API
uv run python -m kortravelweather.cli init-db
# 운영/배포 DB는 위 명령 대신 `uv run alembic upgrade head`를 먼저 적용한다.
PYTHONPATH=packages/kor-travel-weather-api/src uv run uvicorn kortravelweather_api.app:app --reload --port 14101

# Dagster code location
PYTHONPATH=packages/kor-travel-weather-dagster/src uv run dagster dev -m kortravelweather_dagster.definitions -p 14102
```

KMA live 수집에는 `KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY`가 필요하다. 외부
provider는 `KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS`로 선택하며, Open-Meteo와 wttr.in은
키 없이 사용할 수 있다. WeatherAPI, OpenWeatherMap, Visual Crossing, Tomorrow.io,
Weatherbit.io, Weatherstack, AccuWeather는 각 provider API key가 필요하다. 키가
없을 때도 fixture/mock 테스트와 admin UI는 동작하며, live asset은 명확한 credential
오류로 중단된다. 데이터베이스는 PostgreSQL만 지원하며
`KOR_TRAVEL_WEATHER_DATABASE_URL`에 PostgreSQL DSN을 지정한다. 로컬 Compose는
`127.0.0.1:14100`으로 PostgreSQL을 노출한다. 운영 API는
`https://weather-api.digitie.mywire.org`, Dagster는
`https://weather-dagster.digitie.mywire.org`, admin web은
`https://weather.digitie.mywire.org`에서 제공한다.

Admin frontend:

```bash
cd packages/kor-travel-weather-admin/frontend
cp .env.example .env.local  # set WEATHER_API_INTERNAL_URL/WEATHER_ADMIN_TOKEN
npm ci
npm run dev                 # http://127.0.0.1:14105
```

운영 UI는 `kor-travel-map` admin과 같은 좌측 rail·카드형 page header·4pt spacing과
navy design token을 사용한다.
`/weather`는 위치 목록과 지도를 함께 보여 주며, 지도 marker를 선택하면 최신
projection과 forecast preview가 오른쪽 inspector에 열린다. `/login`은 UI 계정으로
서명된 HttpOnly session을 만들고, `/api-test`는 같은 세션의 server-side proxy를
통해 health·catalog·provider·sync-run API를 실행한다. `/admin/dagster`에서는
repository, schedule 상태와 최근 run을 확인하고 Dagster 원본 화면으로 이동한다.
브라우저에는 backend admin token이 내려가지 않는다.

운영에서 frontend를 외부에 노출하지 말고 reverse proxy/SSO 또는
`WEATHER_UI_USER`·`WEATHER_UI_PASSWORD` Basic Auth를 설정한다. Next proxy는
서버에서만 backend admin token을 주입하며 브라우저에 token을 전달하지 않는다.
Dagster 원본 UI 도메인은 gateway Basic Auth로 보호되며, UI의 Dagster 상태 화면은
인증된 Next server-side proxy를 사용한다.

`ktwctl init-db`는 비어 있는 catalog를 위한 insert-only bootstrap이다. 기존
location의 enabled/좌표/metadata는 admin 소유이므로 재실행해도 덮어쓰지 않는다.

## 저장소 구조

```text
src/kortravelweather/                 공용 도메인·설정·repository
  models.py                            WeatherLocation/WeatherValue/SyncRun DTO
  repository.py                        SQLAlchemy raw repository (PostgreSQL)
  providers/base.py                     공통 protocol/HTTP/retry/redaction 경계
  providers/external.py                9개 external provider → WeatherValue 정규화
  providers/catalog.py                 provider/dataset catalog
  providers/kma.py                     KMA row → WeatherValue 정규화
  cli.py                               schema 초기화·fixture 명령

packages/kor-travel-weather-api/      FastAPI/OpenAPI REST backend
packages/kor-travel-weather-dagster/  KMA asset·resource·hourly schedule
packages/kor-travel-weather-admin/    Next.js 운영 UI와 server-side API proxy
packages/python-airkorea-api/         python-airkorea-api 0.4 provider client snapshot
alembic/                               schema migration
docs/                                 architecture, ADR, ETL, runbook, integration 계약
tests/                                unit/API/Dagster 계약 테스트
```

대기질 공급자가 필요한 소비자는 [`packages/python-airkorea-api`](packages/python-airkorea-api)
에 함께 보존한 `airkorea` 패키지를 독립적으로 설치·사용할 수 있다. 이 snapshot은
`python-airkorea-api` 커밋 `9b00dd654f248821798688a1e4afb6edbfd4779f`에서 복사했으며,
weather fact 저장소와는 결합하지 않는다.

## 소비자 계약

- `GET /v1/weather/locations`: 위치 카탈로그
- `GET /v1/weather/locations/{location_id}/latest`: 최신 metric 묶음
- `GET /v1/weather/locations/{location_id}/forecast`: 시각·dataset·metric 필터가 있는 timeline
- `GET /v1/weather/nearby?lat=...&lon=...`: 위치 반경 검색과 각 위치의
  current/forecast/alert/측정소 bundle
- `GET /v1/weather/resolve?lat=...&lon=...`: 요청 좌표에서 가장 가까운 AirKorea
  측정소와 모든 연결 provider의 current/forecast/특보 bundle
- `/v1/admin/*`: 위치와 수집 이력 관리 (admin token 보호)
- `/v1/admin/providers`: credential configured 여부와 dataset catalog (secret 비노출)
- `/v1/admin/provider-credentials`: provider API key 암호화 저장·교체·삭제
- `/health`, `/version`: 의존성 없는 liveness와 버전 확인

응답은 `{data, meta}` envelope이며 `meta.request_id`로 로그와 Dagster run을 연결한다.
자세한 내용은 [`docs/architecture/rest-api.md`](docs/architecture/rest-api.md)와
[`docs/integration-map.md`](docs/integration-map.md)를 참고한다.

UI 정보구조와 화면별 상태/반응형 계약은
[`docs/architecture/admin-ui.md`](docs/architecture/admin-ui.md)에 정리한다.

## 원본 이식 기록

기반 원본은 `digitie/kor-travel-map`의 2026-08-30 최신 `main` 커밋
`41aaa86c43c75e8d9e26d73917008292db0bcb5a`다. 특히 weather DTO의 두 시간축
(`forecast_style`/`timeline_bucket`), KMA raw payload 보존, `kma_weather.py`의
격자 dedupe·retry 패턴, admin weather panel의 loading/error/stale UX를 날씨 전용
계약에 맞춰 이식했다. 현재 MVP에는 durable base-time cursor가 없으며, source
response idempotency와 run overlap guard로 중복 publish를 막고 매 실행에서 응답을
재검증한다. 호출 비용을 줄이는 cursor는 후속 운영 범위다.

## 라이선스

GPL-3.0-or-later. Provider 원천 데이터와 API 응답은 각 기관의 이용약관을 따른다.
