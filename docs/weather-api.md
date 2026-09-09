# Weather API contract

`kor-travel-weather` is a provider-independent, read-optimized weather source.
The API keeps immutable source responses and normalized facts together so a
consumer can resolve one requested coordinate to the nearest published
measurement point, then read all available provider observations, forecasts,
and KMA advisories from that point.

## Base URL and authentication

The public API is served under `/v1/weather` by `weather-api`. Public reads do
not require a token. `/v1/admin/*` requires the `x-admin-token` header and is
for catalog, provider credential, and run operations only.

All timestamps are ISO-8601 with an explicit timezone. `target_at` is the
instant represented by a fact; `known_at` is when this revision was received.
Provider and source identifiers are returned with each value so consumers can
choose a preferred source without losing provenance.

## Coordinate resolution

Use `GET /v1/weather/resolve?lat=37.5665&lon=126.9780` when a user supplies a
coordinate. The response selects the nearest enabled AirKorea measurement
anchor within the requested radius and returns:

```json
{
  "data": {
    "requested": {"latitude": 37.5665, "longitude": 126.978},
    "location": {"location_id": "airkorea-jongno", "name": "종로구", "latitude": 37.572025, "longitude": 127.005028},
    "measurement_point": {"provider": "python-airkorea-api", "station_name": "종로구", "distance_km": 2.4},
    "latest": [],
    "forecast": [],
    "alerts": []
  }
}
```

`latest` contains current/observed values from every enabled source, and
`alerts` contains the active KMA weather-warning facts. The point metadata is
an explicit allow-list; private catalog metadata is never exposed.

`forecast` means different things on the two bundle routes, so parse them
separately:

- `/resolve` returns this point's newest 2000 projected rows per source,
  newest first, which includes recent past targets as well as future ones. It
  is the full-history bundle for one coordinate.
- `/nearby` returns upcoming values only, read forward from the current hour
  (see below).

`alerts` is read on its own budget on both routes, so the two never disagree
about the warning state of the same coordinate.

`GET /v1/weather/nearby` remains the batch form for map views. It returns the
same `latest`, `forecast`, `alerts`, and `measurement_point` fields for each
nearby anchor, ordered by distance. Use `limit` and `radius_km` to bound a map
viewport request.

Every nearby row carries a whole bundle, so the response shares one row budget
across the locations it returns instead of giving each a fixed cap: a wide
request gets a shorter forecast horizon per location in exchange for a bounded
body. The forecast is read forward from the current hour, so the cap shortens
the horizon from its far end rather than from the near end. How far it reaches
depends on how densely a location is forecast: at `limit=100` the cap is 25
rows per location, which for a multi-metric provider is only the next few
target hours. The caps actually applied are published in `meta.bundle`
(`latest_per_location`, `forecast_per_location`), and a single-location request
keeps the full depth. A per-location floor keeps a wide request useful, so the
worst-case body is larger than the row budget alone suggests: at `limit=100`
that is 60 current + 25 forecast rows per location, about 6-8 MB. Follow up on `/resolve` or
`/v1/weather/locations/{id}/forecast` when one location needs everything.

`alerts` is read on its own budget and is never shortened by `limit`. A warning
is announced once and stays active for as long as its validity window says,
while observations keep arriving behind it, so a shared cap would hide exactly
the warnings a map needs to show.

`GET /v1/weather/markers?location_id=...` is a bounded marker projection. Pass
up to 500 enabled location IDs (the admin map sends batches of 500); each item
contains only the current metrics and KMA alert facts needed to choose an icon
and severity badge. It is safe to refresh on every map viewport update.

## Location and history routes

- `GET /v1/weather/locations` — enabled location catalog (paginated).
- `GET /v1/weather/locations/{location_id}/latest` — current projection,
  deduplicated to the newest immutable revision per logical metric.
- `GET /v1/weather/locations/{location_id}/forecast` — forecast/history query;
  supports `from`, `to`, `dataset_key`, `metric_key`, and `history=true` for
  explicit revision history. This is a timeline read, so rows come back
  chronologically and `limit` truncates the far end. Callers that want upcoming
  values must pass `from`; without it the window opens at the oldest row the
  projection still holds and the response can be entirely in the past.
- `GET /v1/weather/resolve` — nearest-anchor all-source bundle described above.

## Hourly ingestion and providers

The Dagster `hourly_kma_weather`, `hourly_airkorea_weather`, and
`hourly_external_weather` schedules run in Asia/Seoul. KMA is collected by the
existing grid pipeline, including `kma_weather_alerts`. AirKorea refreshes its
station catalog and measurements independently at the ten-minute mark; the
external schedule then collects every enabled non-KMA provider from the latest
enabled AirKorea station anchors. An AirKorea quota or provider outage must
not prevent already-known anchors from receiving external updates. Providers
with no configured key (for example Open-Meteo and wttr.in) remain keyless;
keyed providers are skipped with an auditable run result until their admin
credential is configured.

## Regional observation networks

Three sources are collected on their own schedule, `twice_daily_regional_weather`
(05:30 and 17:30 Asia/Seoul):

| Provider | Dataset | What |
| --- | --- | --- |
| `python-khoa-api` | `khoa_beach_index` | 해수욕장별 파고·수온·기온·풍속 일별 **예보** |
| `python-krforest-api` | `krforest_mountain_weather` | 산악관측소 기온·습도·기압·강수·지면온도·풍향풍속 관측 |
| `python-krex-api` | `krex_restarea_weather` | 고속도로 휴게소 기온·습도·풍속·강수·적설·이슬점 관측 |

They differ from the hourly providers in shape: one call returns every station
in the country and each row carries its own coordinates, so there is no station
catalog to refresh and no per-location fan-out. Anchors are created insert-only
under the `khoa-`, `krforest-` and `krex-` id prefixes, so an administrator can
disable one without the next run restoring it.

The KHOA rows are recorded as forecasts (`forecast_style=short`), not
observations — they are next-day outlooks, and filing them as current readings
would make a bundle present tomorrow's wave height as the present sea state.

They run twice a day rather than hourly because the hourly path already
produces roughly three million facts a day and these sources publish a few
times a day at most. `KOR_TRAVEL_WEATHER_REGIONAL_MAX_RECORDS` bounds one run;
a run that hits the value budget cuts between stations, never inside one, and
reports `values_truncated`.

KHOA and 산림청 authenticate with the shared data.go.kr service key. 한국도로공사
does not — it needs `KOR_TRAVEL_WEATHER_KREX_API_KEY` (a data.ex.co.kr key), and
without it that one asset fails while the other two still run.

## Retention

`weather_values` and `weather_source_records` are append-only, which is why a
published fact can be trusted and also why, left alone, they only grow. The
Dagster `daily_weather_retention` schedule (03:20 Asia/Seoul, after the hourly
ingests) deletes history older than `KOR_TRAVEL_WEATHER_RETENTION_DAYS`
(default 2).

Two things it deliberately does not do. It never deletes a fact the
current-value projection still points at, so a location that stops reporting
keeps its last reading however old it is. And it never updates a row — the
immutability trigger still refuses that, including inside the purge's own
transaction; the purge opens a `SET LOCAL` permission that applies to `DELETE`
only and expires with the transaction.

A run stops at `KOR_TRAVEL_WEATHER_RETENTION_MAX_BATCHES` and leaves the rest to
the next one, reporting `truncated: true` and logging a warning. A run that
reports it every night means the ingest rate exceeds what the window can hold,
and the table will keep growing no matter how often the job runs.

The provider catalog currently includes WeatherAPI, OpenWeatherMap, Open-Meteo,
Visual Crossing, Tomorrow.io, Weatherbit, Weatherstack, AccuWeather, and
wttr.in. KMA weather warnings are stored as `kma_weather_alerts` facts and are
available through `alerts` and the map marker warning badge. KMA warning
issuing offices are selected by each target's `metadata.kma_alert_station_id`,
falling back to `KOR_TRAVEL_WEATHER_KMA_ALERT_STATION_ID` (default `108`). A
deployment covering multiple offices must configure one target group per
office; rows carrying an explicit region are only fanned out to matching
anchors.

## Consumer guidance

1. Call `/resolve` for a coordinate and retain `location.location_id` plus
   `measurement_point` in the client state.
2. Render `latest` first; group `forecast` by `target_at` and use `provider` /
   `dataset_key` as the source label.
3. If `alerts` is non-empty, display the highest `severity` and the warning
   text before normal conditions.
4. Treat an empty list as a valid no-data state. Use `known_at` and
   `collected_at` to show freshness, not the browser clock alone.

The checked-in OpenAPI document is generated from the running FastAPI app with
`python scripts/export_openapi.py`; clients should regenerate typed models from
that document rather than infer fields from a single provider.
