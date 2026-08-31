# Integration map

| consumer | reads | owns | does not own |
| --- | --- | --- | --- |
| PinVi | public weather REST/OpenAPI | UI presentation | KMA credentials/ETL |
| kor-travel-map | public weather REST/OpenAPI (`/resolve`, `/nearby`) | map feature/weather presentation | weather fact history |
| Dagster | WeatherLocation catalog, KMA | source/fact publication, sync runs | consumer UI |
| admin UI | admin REST proxy | location enablement, run observation | direct DB/raw SQL |

Consumer는 `source_record_key`로 원본을 추적할 수 있지만 raw payload를 public
응답에서 직접 받지 않는다. 위치 lifecycle은 weather source가 소유하고, 지도
feature/POI identity는 map 프로젝트가 소유한다.

`kor-travel-map`과 PinVi의 adapter는 feature 좌표 → `GET /v1/weather/resolve` →
`measurement_point`, `latest`, `forecast`, `alerts` 순서로 호출한다. 여러 marker를
한 번에 그릴 때는 `/nearby`를 사용한다. 이 프로젝트는 `/v1/features/*` feature
identity를 소유하지 않지만 weather alerts를 KMA `alerts` bundle로 소유한다.
