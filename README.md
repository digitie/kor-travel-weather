# kor-travel-weather

대한민국 전역의 관측·예보·특보를 모아 제공하는 독립 날씨 데이터 소스다. 
`kor-travel-map`의 데이터 계약·운영 패턴을 가져오되, 장소/축제/가격 같은 지도
도메인은 소유하지 않는다. PinVi와 `kor-travel-map`은 저장소를 직접 읽지 않고
REST/OpenAPI를 통해 날씨 데이터를 소비한다.

## 목표

- `python-kma-api`를 통해 기상청 초단기실황·초단기예보·단기예보를 주기적으로 수집한다.
- 위치와 원천 시각을 보존하는 정규화된 weather fact를 멱등적으로 저장한다.
- 공용 REST API로 최신값·예보 timeline·좌표 기반 근접 위치를 제공한다.
- Dagster schedule과 운영 이력을 admin UI에서 확인하고 위치를 관리한다.
- 특정 소비자에 종속되지 않는 provider/dataset/metric 계약과 raw payload 추적성을 유지한다.

## 운영 모델

```text
python-kma-api ──> Dagster asset ──> PostgreSQL/SQLite ──> FastAPI/OpenAPI
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
uv sync --extra dev
cp .env.example .env

# 로컬 SQLite schema + API
uv run python -m kortravelweather.cli init-db
uv run uvicorn kortravelweather_api.app:app --reload --port 12721

# Dagster code location
uv run dagster dev -m kortravelweather_dagster.definitions -p 12722
```

KMA live 수집에는 `KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY`가 필요하다. 키가
없을 때도 fixture/mock 테스트와 admin UI는 동작하며, live asset은 명확한 credential
오류로 중단된다. 기본 DB는 `data/weather.db` SQLite이고 운영에서는
`KOR_TRAVEL_WEATHER_DATABASE_URL`에 PostgreSQL DSN을 지정한다.

Admin frontend:

```bash
cd packages/kor-travel-weather-admin/frontend
npm ci
npm run dev                 # http://127.0.0.1:12725
```

## 저장소 구조

```text
src/kortravelweather/                 공용 도메인·설정·repository
  models.py                            WeatherLocation/WeatherValue/SyncRun DTO
  repository.py                        SQLAlchemy raw repository (SQLite/PostgreSQL)
  providers/kma.py                     KMA row → WeatherValue 정규화
  cli.py                               schema 초기화·fixture 명령

packages/kor-travel-weather-api/      FastAPI/OpenAPI REST backend
packages/kor-travel-weather-dagster/  KMA asset·resource·hourly schedule
packages/kor-travel-weather-admin/    Next.js 운영 UI와 server-side API proxy
alembic/                               schema migration
docs/                                 architecture, ADR, ETL, runbook, integration 계약
tests/                                unit/API/Dagster 계약 테스트
```

## 소비자 계약

- `GET /v1/weather/locations`: 위치 카탈로그
- `GET /v1/weather/locations/{location_id}/latest`: 최신 metric 묶음
- `GET /v1/weather/locations/{location_id}/forecast`: 시각·dataset·metric 필터가 있는 timeline
- `GET /v1/weather/nearby?lat=...&lon=...`: 위치 반경 검색과 최신값
- `/v1/admin/*`: 위치와 수집 이력 관리 (admin token 보호)
- `/health`, `/version`: 의존성 없는 liveness와 버전 확인

응답은 `{data, meta}` envelope이며 `meta.request_id`로 로그와 Dagster run을 연결한다.
자세한 내용은 [`docs/architecture/rest-api.md`](docs/architecture/rest-api.md)와
[`docs/integration-map.md`](docs/integration-map.md)를 참고한다.

## 원본 이식 기록

기반 원본은 `digitie/kor-travel-map`의 2026-08-30 최신 `main` 커밋
`41aaa86c43c75e8d9e26d73917008292db0bcb5a`다. 특히 weather DTO의 두 시간축
(`forecast_style`/`timeline_bucket`), KMA raw payload 보존, `kma_weather.py`의
격자 dedupe·cursor·retry 패턴, admin weather panel의 loading/error/stale UX를
날씨 전용 계약에 맞춰 이식했다.

## 라이선스

GPL-3.0-or-later. Provider 원천 데이터와 API 응답은 각 기관의 이용약관을 따른다.
