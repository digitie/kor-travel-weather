# Development environment

```bash
uv sync --extra dev --extra dagster
cp .env.example .env
set -a; . .env; set +a
export PGPASSWORD="$POSTGRES_PASSWORD"
docker compose up -d db
uv run python -m kortravelweather.cli init-db
PYTHONPATH=packages/kor-travel-weather-api/src uv run uvicorn kortravelweather_api.app:app --reload --port 12101
PYTHONPATH=packages/kor-travel-weather-dagster/src uv run dagster dev -m kortravelweather_dagster.definitions -p 12102
```

Python 명령은 저장소 root에서 실행해 root `.env`와 PostgreSQL Compose DB를 공유한다.
배포 환경을 생략하면 설정은 production으로 간주되어 admin token이 없을 때
기동이 거부된다. 로컬에서는 `.env.example`의 `KOR_TRAVEL_WEATHER_ENV=development`를
그대로 사용한다.
운영 DB에는 `uv run alembic upgrade head`를 먼저 적용한다. `ktwctl init-db`는
기존 catalog를 덮지 않는 insert-only bootstrap이다. frontend만
`packages/kor-travel-weather-admin/frontend`로 이동해 `npm ci && npm run dev`로
12105 포트에 기동한다. package README의 `cd ../..`도 이 root 경계를 명시한다.
