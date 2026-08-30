# Weather source architecture

`kor-travel-weather`는 장소·축제·가격을 소유하지 않는 독립 provider data source다.
`WeatherLocation`은 KMA grid anchor catalog이고 `WeatherValue`는 append-only normalized fact다.

```text
python-kma-api ──(raw response)──> Dagster stage
                                      │ complete manifest
                                      ▼
                              SQLAlchemy repository
                         source_records + weather_values
                                      │
                     FastAPI public/admin REST + OpenAPI
                                      │
                         Next.js operator console
```

API와 Dagster는 같은 DB를 사용하지만 provider I/O와 HTTP를 서로 호출하지 않는다.
외부 consumer(PinVi, kor-travel-map)는 REST contract만 사용한다. 운영 schema는
Alembic이 소유하고 `create_all`은 개발 fixture에서만 사용한다.

## Package boundaries

- `src/kortravelweather`: domain DTO, validation, repository, provider adapters
- `packages/kor-travel-weather-api`: FastAPI app, public/admin routers, OpenAPI
- `packages/kor-travel-weather-dagster`: KMA resources, stage/publish asset, schedule
- `packages/kor-travel-weather-admin/frontend`: server-side API proxy와 operator UI
