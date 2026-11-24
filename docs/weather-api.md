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

KMA runs one schedule per dataset, each in Asia/Seoul: `hourly_kma_ultra_short_forecast`
(`0 * * * *`), `hourly_kma_ultra_short_nowcast` (`20 * * * *`),
`kma_short_forecast_publish_hours` (`15 2,5,8,11,14,17,20,23 * * *`, matching KMA's
real 단기예보 publish times), `kma_mid_forecast_publish_hours` (`30 6,18 * * *`), and
`hourly_kma_weather_alerts` (`5 * * * *`). `hourly_airkorea_weather` refreshes
AirKorea's station catalog and measurements independently at `35 * * * *`. The
ultra-short forecast, nowcast and AirKorea take turns because they lock the same
station anchors; see [Dagster boundary](architecture/dagster-boundary.md#매시-station-lock-job의-시간-분리).
A `three_hourly_<provider>_<part>_weather`
schedule per sweeping external provider (Open-Meteo, OpenWeatherMap, WeatherAPI; current and
forecast each) then collects from the latest enabled AirKorea station anchors. An AirKorea
quota or provider outage must not prevent already-known anchors from receiving external
updates. The other external providers (Visual Crossing, Tomorrow.io, Weatherbit, Weatherstack,
AccuWeather, wttr.in) keep their jobs for manual runs but have no schedule until they are added
to the slow sweep group (`SLOW_EXTERNAL_PROVIDER_KEYS`), which also creates their schedules.
wttr.in needs no key but is off by default. A 429 waits once for a `Retry-After` of 30 s or
less; otherwise the run stops that provider's sweep and records it as deferred for the next
three-hourly cycle.

What "deferred" and "partial" do and do not keep:

- A sweep the provider stops (rate limit, auth, five failed locations in a row, or more than
  20 % of the targets failed) publishes every response it already received, including those
  still staged below the publish batch limit, then closes its `weather_sync_runs` row as
  `failed`. Dagster shows a 429 as a SUCCESS run tagged `kortravel/collection-outcome=deferred`,
  but the native row is `failed`, so the run's export cohort is not approved and its export
  attempts are retired. The Map exports that read that dataset (`<provider>_current`: current
  and daily summaries; `<provider>_forecast`: external forecasts) keep serving the previously
  approved values with `source_status=stale` until the next successful sweep.
- A client, schema, server or network error at one location skips only that location. The
  facts and the native run succeed and Dagster tags the run `partial`, but the cohort declared
  before the first request still lists the skipped location, whose export head stays on an
  earlier run. The cohort is therefore unconfirmed and those Map exports stay `stale` (a request
  without a provider selection falls back to the previous approved snapshot) until a sweep
  reaches every location. Skipping does not make Map fresher than a failed run did. The
  skipped count is recorded only in the Dagster metadata (`locations_failed`,
  `location_failure_counts`), not in `weather_sync_runs`.

## Regional observation networks

Collected twice a day (05·17시 Asia/Seoul), one job and schedule per source so one
source's failure does not hold back the others' promotion: `twice_daily_krforest_mountain`
(:30), `twice_daily_khoa_beach_index` (:40), `twice_daily_krex_restarea` (:45),
`twice_daily_krforest_dust` (:50). Each schedule also ticks 1 and 3 hours later, and those
catch-up ticks run only when the regular tick left no successful run. DNS·connection·timeout
errors are retried in the same run (up to 3 times, 60–240 s with jitter). The four jobs share
the `kortravelweather/run_group=regional_weather` tag, which the Dagster instance caps at one
run, so they still take one weather run slot at a time like the old bundled job.

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
coordinates live — a measurement names a station code and nothing else. A key
approved for the first and not the second fetches perfectly good numbers that
cannot be placed anywhere, so the asset reports a skip naming the dataset to
apply for rather than failing.

The vendor returns a window oldest-first and the window spans days, so the fetch
pages **backwards** from `total_count`. Paging forward and stopping at the
record budget collects the oldest rows in the window, which the recency cutoff
then discards — the first run against an approved catalog fetched three-day-old
rows and published nothing.

`python-krex-api` needs a data.ex.co.kr key
(`KOR_TRAVEL_WEATHER_KREX_API_KEY`), which is a different key from the shared
data.go.kr one and is not a fallback. It is off by default; add it to
`KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS` with the key to turn it on. A provider
that is not in that list is skipped before its credential is even requested.

**A data.ex.co.kr edge block still fails the krex step, labelled.** When the
edge answers with its "Request Blocked" page (HTTP 400/403, HTML) the step fails
as before, but its error and metadata carry `failure_kind=upstream_blocked` and
`http_status`, and the request counts as
`ktw_provider_requests_total{outcome="blocked"}` (`docs/observability.md`).
The other regional assets are separate steps and are not affected by it. Every
other krex error fails the step unlabelled, as it always did.

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

The Dagster `daily_weather_retention` schedule (01:45 Asia/Seoul, the measured
low point of the ingest schedules) keeps `KOR_TRAVEL_WEATHER_RETENTION_DAYS`
days, default 2. Production (n150) runs 7 since 2026-10-13 (owner decision; it
was 16). PinVi's minimum history requirement moved to 7 days at the same time,
so 7 is also the floor for production: going lower breaks PinVi. Each run also
creates the coming week's partitions: a missing partition sends rows to DEFAULT, where retention can never
reach them.

Ingestion never pauses, and creating, detaching or dropping a partition needs
the fact table to itself for a moment. So each of those steps is one short
transaction for one day, takes its locks in the ingest's order (source records
and locations, then facts, then the projection), and gets a few attempts spaced
by waits for a moment when nobody holds the fact table. A day that still loses
is **deferred**: listed in the run's metadata (`partitions_deferred`,
`partitions_not_created`) and retried by the next run, while the run stays
green. The run turns `partial` -- or fails, if it also made no progress -- only
for work missed on consecutive nights (`partitions_overdue`, or a forward
window down to three days). A miss is a day that was due and not done -- tried
and lost, or skipped by the circuit breaker: it is marked with a table comment,
and is overdue only when an earlier run had marked it -- a night the job did
not run is not a miss. The comment gets three short tries; one that still loses
its lock race is listed in `partitions_unmarked`, recorded as an asset
observation (no table lock, written even when the run fails) and handed to the
next run, which counts it as marked. Detach and drop
are separate transactions; while a detached day waits for its drop no further
day is detached (those held-back days are not marked: the waiting drop is the
miss), so at most one ever waits, and only the source records it
cites are held back from the source purge. After three deferred steps in a row
of one kind -- forward creation, or detach/drop, each with its own breaker, so
a stuck forward step cannot starve retention -- the run stops trying that kind
(`partition_ddl_stopped`) rather than spend its 2h cap. `KorTravelWeatherRetentionStale` pages when the
oldest dated partition (`ktw_oldest_partition_age_days`) is more than
retention + 2 days old.

`DETACH PARTITION ... CONCURRENTLY` would avoid the exclusive lock on the
parent, but PostgreSQL refuses it while the table has a DEFAULT partition, and
this one keeps DEFAULT on purpose (its floor is what lets a partition be created
without scanning it, and what the replay skip reads). Retention uses a plain
`DETACH`, one day per transaction.

**지도 읽기를 세우는 구간 줄이기(2026-11-24, Map 호출 검토 L1·L2·L3).** ACCESS EXCLUSIVE 요청이
대기열에 있는 동안에는 뒤에 온 읽기도 그 뒤에 선다. 그래서 야간 단계는 다음처럼 lock 구간을 줄인다.

1. 첫 시도 앞에서도 조용한 순간을 기다린다(`_await_quiet_tables`, 예전에는 두 번째 시도부터).
   floor 확인과 SHARE UPDATE EXCLUSIVE의 FK VALIDATE는 수집·읽기와 충돌하지 않아 첫 대기를 건너뛴다.
2. 표 lock을 `BRIEF_DDL_LOCK_TIMEOUT_MS`(400ms)까지만 기다리고 물러나 다음 조용한 순간에 다시
   시도한다. 예전에는 `ddl_lock_timeout_ms`(deadlock_timeout의 세 배, 기본 3초)를 시도마다 기다렸다.
   짧은 대기에 진 뒤 차단자가 autovacuum worker뿐이면 그 lock만 `ddl_lock_timeout_ms`까지 다시
   기다린다. autovacuum은 대기자가 deadlock_timeout을 넘겨 기다려야 deadlock 검사가 취소하기 때문이다.
   차단자는 `pg_locks`의 granted 보유자를 충돌표로 거르고 `pg_stat_activity.backend_type`으로 가린다.
   이때 LOCK 전과 짧은 대기에 진 직후에 한 번씩 보유자를 읽고, 직후의 보유자 중 **LOCK 전부터 같은
   transaction(`pg_locks.virtualtransaction`)이 쥐고 있던 것과 autovacuum worker만** 센다. 부모에 거는
   LOCK은 부모를 먼저 잡고 autovacuum이 쥔 파티션에서 기다리므로, 그동안 온 지도 reader·수집은 우리
   뒤에 줄을 섰다가 savepoint로 되돌리는 순간 lock을 받아 직후의 보유자에 섞인다. 그들까지 세면 읽기가
   도는 밤에는 autovacuum만 막는 경우를 놓쳐 detach·미래 파티션 생성이 autovacuum을 끝내 이기지 못한다.
   `pg_stat_activity`는 transaction 안에서 처음 읽은 snapshot에 고정되므로 읽을 때마다
   `pg_stat_clear_snapshot()`으로 버린다. superuser나 `pg_read_all_stats`가 아닌 role에는 autovacuum의
   `backend_type`이 NULL로 보여 이 예외가 동작하지 않는다(연기 쪽으로 간다). compose의
   `POSTGRES_USER`는 superuser다.
3. 단기예보 보존 검사(최대 5초)를 표 lock 전에 하고, lock 뒤에는 300ms 상한의 재확인만 한다. 떼어낸
   표의 DROP은 그 표만 SHARE로 잠근 뒤 참조 표 lock 전에 한 번만 검사한다
   ([단기예보 보존](shortterm-retention.md)).

운영자 DEFAULT 교체 스크립트·migration·수집의 lock 대기는 그대로 `ddl_lock_timeout_ms`다. 로컬
PostgreSQL 16 시험(`tests/test_retention_under_ingest.py`)은 수집에 진 DDL 시도 뒤에 선 읽기의 가장 긴
대기가 짧은 상한에서 1.5초 미만이고 예전 상한에서 2초 이상임을 확인한다(2026-11-24 Windows 개발
PC·WSL docker PostgreSQL 16 로컬 3회: 0.37~0.40초 대 약 2.98초). 운영(야간 유지보수 중 지도 읽기
p95·lock 대기)은 아직 재지 않았다.

**포인터·원천 정리의 batch(2026-11-24, Map 호출 검토 L4).** 만료 일자를 가리키는 현재값 포인터와
아무것도 인용하지 않는 만료 원천 기록은 예전에 각각 상한 없는 DELETE 한 문장이었다. 밀린 뒤 첫
실행이면 수백만 행의 WAL·bloat가 한 transaction에 몰리고, 그동안 같은 포인터 행을 쓰는 수집
CAS가 그 행 lock 뒤에 섰다. 지금은 기본키 순서의 keyset batch(1,000행)마다 commit하고, batch마다
`weather_history_purge` advisory lock을 먼저 기다려 잡은 뒤 문장 10초·lock 1초 상한을 건다. 지우는
대상은 그대로다(보존할 최근6시간·미래 단기예보 포인터, 떼어 두고 아직 못 지운 표가 인용하는 원천은
남긴다). batch가 lock/문장 상한·deadlock에 지면 같은 위치에서 세 번 더(2·4·8초 뒤) 시도하고, 그래도
지면 앞 batch의 commit은 둔 채 멈춘다(`pointers_deferred`·`sources_deferred`, 실행은 `deferred`).
포인터를 끝내지 못한 밤에는 만료 일자를 떼지 않는다: 남은 포인터가 가리키는 fact를 떼면 FK 검증이 그
포인터를 상한 없는 DELETE(`delete_dangling_projection_rows`)로 지우게 되기 때문이다. 그 날들은
`partitions_deferred`로 표시되고 다음 밤에도 못 하면 overdue다. 수집 run에 연결된 원천은 지우지
않으므로 원천 후보는 오래된 원천 이력 거의 전부다. 그래서 원천 단계는 실행당 1,800초 예산만큼 읽고
멈춘 위치(`sources_resume_after`)를 Dagster 실행 기록으로 다음 실행에 넘겨 이어 읽는다. 원천 batch
연기가 연속 2회면 실행은 `partial`이다. 자세한 근거는 [쓰기 경로 상한](write-path-bounds.md).

**A replay outlives its first fetch's day.** A response the store already holds
is replayed with its first fetch time as `known_at`, so an identical re-fetch is
a no-op instead of a new row. Retention drops days, not responses: an alert in
force for longer than the window, or a forecast nobody revised, is still served
by the provider after its first fetch's partition is gone, and its source record
survives too (every run that fetches it cites it). Such a replay -- source
stored before this batch, first-fetch date below the oldest kept partition
(the *horizon*), stored fact gone -- is **re-dated to this fetch**: a new row
with `known_at = collected_at =` the re-fetch time, as a first fetch would get.
Later replays find that row (the earliest at or above the horizon) and replay
against it, so a response re-fetched hourly makes one row per retention period.
This keeps the bitemporal rules: facts stay append-only, source records are
untouched, and `known_at` only moves forward for a `value_id` -- it is when the
store knew the fact, and the oldest knowledge it still holds is the re-fetch.
A replay whose own fetch time is below the horizon too (an old batch published
again) and a response first stored by a late batch with an old `fetched_at` are
past retention and skipped, as before. The accepted cost: if a provider serves
an old response again after a newer revision of the same logical point, the
re-dated old one becomes the projection's newest -- it is what the provider
serves now.

Two consequences worth stating plainly.

**A location that stops reporting loses its current value** once its last
reading ages out. A partition cannot be dropped selectively, so the projection's
pointers into it are deleted first — which is what "we keep N days" actually
means (until a re-fetch re-dates a value the provider still serves, above). The
previous batched delete spared any fact a pointer named, and so kept stale
readings for ever.

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
