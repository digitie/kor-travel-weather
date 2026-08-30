# kor-travel-weather API

FastAPI의 public weather catalog/fact와 token-protected admin 위치 관리
surface다. API는 `kortravelweather.WeatherValue`의 normalized 필드만 공개하며
원천 raw payload는 `source_record_key`로만 추적한다.

```bash
cd packages/kor-travel-weather-api
uv sync --extra dev
uv run uvicorn kortravelweather_api.app:app --reload --port 12721
uv run python scripts/export_openapi.py
```

공개 경로는 `/v1/weather/*`, 운영 경로는 `/v1/admin/*`이다. 운영에서는
`KOR_TRAVEL_WEATHER_ADMIN_TOKEN`과 PostgreSQL DSN을 반드시 설정한다. admin에는
삭제 route가 없으며 위치는 `PATCH enabled=false`로 비활성화한다.
