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

## 지도 행정 경계

관리자 BFF의 `/api/region-boundary`는 Geo의 기존 `/v2/reverse`·`/v2/search`를 서버에서
호출한다. 법정 코드·단계·실제 Polygon/MultiPolygon을 검산하고128개/16MiB/6시간 메모리
캐시를 둔다. Geo 원본 경계는 Geo DB가 소유하며 Weather는 Geo DB에 직접 접근하거나
수집·변환 로직을 복제하지 않는다. `WEATHER_GEO_INTERNAL_URL`·`WEATHER_GEO_API_KEY`는
웹 서버 비공개 설정이고, 키를 브라우저·URL로 보내지 않는다. 미연결·자료 없음은 기존 날씨
조회 실패로 바꾸지 않는다. [검증 범위](../reports/2026-11-13-selected-region-boundary.md).
