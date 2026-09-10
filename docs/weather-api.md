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

Collected on their own schedule, `twice_daily_regional_weather` (05:30 and 17:30
Asia/Seoul):

| Provider | Dataset | What |
| --- | --- | --- |
| `python-khoa-api` | `khoa_beach_index` | 해수욕장별 파고·수온·기온·풍속 일별 **예보** |
| `python-krforest-api` | `krforest_mountain_weather` | 산악관측소 기온·습도·기압·강수·지면온도·풍향풍속 |
| `python-krforest-api` | `krforest_dust` | 청정넷(AICAN) PM10·PM2.5·PM1.0과 기온·습도·풍향풍속 |
| `python-krex-api` | `krex_restarea_weather` | 고속도로 휴게소 기온·습도·풍속·강수·적설·이슬점 |

They differ from the hourly providers in shape: one call returns every station
in the country and each row carries its own coordinates, so there is no station
catalog to refresh and no per-location fan-out. Anchors are created insert-only
under the `khoa-`, `krforest-`, `krforest-dust-` and `krex-` id prefixes, so an
administrator can disable one without the next run restoring it.

The KHOA rows are recorded as forecasts (`forecast_style=short`), not
observations — they are next-day outlooks, and a beach carries a morning and an
afternoon one for the same date, so they take target times of 09:00 and 15:00
KST rather than sharing midnight.

**Mountain readings need an explicit observation time.** The vendor returns a
row per station either way, but every metric comes back as `"-"` unless a
ten-minute mark is named. The fetch asks for the current mark and walks back up
to two hours until it finds one with readings; a mark that has rows and no
readings is treated as no mark at all, because publishing it would anchor
several hundred stations and no facts.

**청정넷 needs two data.go.kr approvals, and they are separate.** `15078005`
carries the readings; `15078013` is the station catalog and the only place the
coordinates live. A key approved for the first and not the second fetches
perfectly good numbers that cannot be placed anywhere, so the asset reports a
skip naming the dataset to apply for rather than failing — the remedy takes
days, and a schedule that goes red every morning helps nobody.

`python-krex-api` needs a data.ex.co.kr key
(`KOR_TRAVEL_WEATHER_KREX_API_KEY`), which is a different key from the shared
data.go.kr one and is not a fallback. It is off by default; add it to
`KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS` with the key to turn it on. A provider
that is not in that list is skipped before its credential is even requested.

`KOR_TRAVEL_WEATHER_REGIONAL_MAX_RECORDS` bounds one run; a run that hits the
value budget cuts between stations, never inside one, and reports
`values_truncated`. A run that fetches rows and publishes none reports
`produced_nothing` and logs a warning.

## Retention

`weather_values` is partitioned by day on `known_at`, and retention drops the
days that have fallen out of the window rather than deleting the rows in them.
Deleting three million facts a night writes a tombstone and a WAL record for
every one and then needs a vacuum to return the space; dropping a partition is a
catalog change, so nothing is scanned, nothing is vacuumed, and the space comes
back at once. That difference is why the table could reach 33 GB with no way to
shed anything.

The Dagster `daily_weather_retention` schedule (03:20 Asia/Seoul, after the
hourly ingests) keeps `KOR_TRAVEL_WEATHER_RETENTION_DAYS` days, default 2. Each
run also creates the coming week's partitions: a missing partition sends rows to
DEFAULT, where retention can never reach them.

Two consequences worth stating plainly.

**A location that stops reporting loses its current value** once its last
reading ages out. A partition cannot be dropped selectively, so the projection's
pointers into it are deleted first — which is what "we keep two days" actually
means. The previous batched delete spared any fact a pointer named, and so kept
stale readings for ever.

**`rows_outside_any_partition` is the field to watch.** Rows in the DEFAULT
partition are never dropped, so a non-zero count is the table quietly starting
to grow again. The asset logs a warning; nothing else in the result
distinguishes it from a healthy run.

History remains append-only. The immutability trigger still refuses every
UPDATE, including inside the purge's own transaction; the purge opens a
`SET LOCAL` permission that applies to `DELETE` only — used now just for source
records, which are small and not partitioned.

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
