import type { RegionalAlertEvents } from "@/lib/alert-event-types";
import { failureMessage, readBody } from "@/lib/http";

export type PageMeta = {
  limit: number;
  offset: number;
  returned: number;
  total: number | null;
};

/**
 * Per-location row caps a multi-location bundle applied. `/v1/weather/nearby`
 * shares one whole-response row budget across the locations it returns, so a
 * wide request carries fewer rows per location than a narrow one.
 */
export type BundleMeta = {
  latest_per_location: number;
  forecast_per_location: number;
};

export type ApiEnvelope<T> = {
  data: T;
  meta: {
    request_id: string;
    generated_at: string;
    duration_ms: number;
    page?: PageMeta | null;
    bundle?: BundleMeta | null;
    alert_scope_unverified?: boolean;
  };
};

export type Location = {
  sido_name?: string | null;
  sigungu_name?: string | null;
  location_id: string;
  name: string;
  latitude: number;
  longitude: number;
  nx: number | null;
  ny: number | null;
  region_code: string | null;
  enabled: boolean;
  metadata: Record<string, unknown>;
};

export type WeatherKind = "alert" | "nowcast" | "ultra_short" | "short" | "mid" | "observed";

export type WeatherValue = {
  value_id: string;
  location_id: string;
  provider: string;
  dataset_key: string;
  weather_domain: string;
  forecast_style: string;
  timeline_bucket: string | null;
  metric_key: string;
  metric_name: string | null;
  source_metric_key: string | null;
  source_metric_name: string | null;
  value_number: number | null;
  value_text: string | null;
  unit: string | null;
  severity: string | null;
  issued_at: string | null;
  valid_at: string | null;
  valid_from: string | null;
  valid_until: string | null;
  observed_at: string | null;
  target_at: string;
  known_at: string | null;
  normalization_version: string;
  collected_at: string;
  source_record_key: string;
};

export type MeasurementPoint = {
  provider: string;
  station_id: string | null;
  station_name: string;
  address: string | null;
  network: string | null;
  latitude: number;
  longitude: number;
  distance_km: number;
};

export type NearbyWeather = Location & {
  alert_scope_unverified?: boolean;
  distance_km: number;
  measurement_point: MeasurementPoint | null;
  latest: WeatherValue[];
  forecast: WeatherValue[];
  alerts: WeatherValue[];
};

export type ResolvedWeather = {
  alert_scope_unverified?: boolean;
  requested: { latitude: number; longitude: number };
  location: Location;
  distance_km: number;
  measurement_point: MeasurementPoint | null;
  latest: WeatherValue[];
  forecast: WeatherValue[];
  alerts: WeatherValue[];
  source_locations: Location[];
};

export type WeatherMarker = {
  alert_scope_unverified?: boolean;
  location_id: string;
  measurement_point: MeasurementPoint | null;
  latest: WeatherValue[];
  forecast?: WeatherValue[];
  alerts: WeatherValue[];
};

const SYNC_RUN_STATUS_LABELS: Record<string, string> = {
  success: "성공",
  failed: "실패",
  running: "진행 중",
};

export function syncRunStatusLabel(status: string): string {
  return SYNC_RUN_STATUS_LABELS[status] ?? status;
}

export type SyncRun = {
  run_id: string;
  provider: string;
  dataset_key: string;
  status: string;
  started_at: string;
  heartbeat_at: string | null;
  finished_at: string | null;
  locations_total: number;
  grids_fetched: number;
  mid_groups_fetched: number;
  requests_fetched: number;
  values_loaded: number;
  error: string | null;
};

export type ProviderDataset = {
  key: string;
  label: string;
  description: string;
  endpoint: string;
  cadence: string;
  forecast: boolean;
};

export type Provider = {
  provider: string;
  label: string;
  auth_required: boolean;
  credential_configured: boolean | null;
  base_url: string;
  datasets: ProviderDataset[];
};

type RequestOptions = Omit<RequestInit, "body"> & { body?: unknown };

/**
 * GET 한 시도의 브라우저 상한(헤더+본문). BFF 헤더 예산(15초)보다 길게 두어 보통은 BFF의
 * 504·retry-after가 먼저 도착한다. 이 상한은 BFF 자체가 멈췄거나 본문 전송이 멈춘 경우의 끝이다.
 */
export const GET_ATTEMPT_TIMEOUT_MS = 20_000;
/** GET 첫 시도 뒤 최대 재시도 횟수. 총 시도는 3번이다. */
export const GET_MAX_RETRIES = 2;
const RETRYABLE_STATUSES = new Set([502, 503, 504]);
const RETRY_BASE_DELAY_MS = 500;
/** Retry-After가 이보다 길면 기다리지 않고 바로 실패를 보여준다. */
const MAX_RETRY_AFTER_MS = 10_000;

function retryAfterMs(header: string | null, now: number): number | null {
  const value = header?.trim();
  if (!value) return null;
  if (/^\d+$/.test(value)) return Number(value) * 1000;
  const at = Date.parse(value);
  return Number.isNaN(at) ? null : Math.max(0, at - now);
}

/**
 * `retry`번째 재시도(0부터) 전 대기(ms). 지수 backoff(0.5s, 1s)와 Retry-After 중 큰 값에
 * backoff의 절반까지 jitter를 더한다. Retry-After가 10초를 넘으면 null(재시도하지 않음)이다.
 */
export function retryDelayMs(
  retry: number,
  retryAfter: string | null,
  random: () => number = Math.random,
  now: number = Date.now(),
): number | null {
  const backoff = RETRY_BASE_DELAY_MS * 2 ** retry;
  const hinted = retryAfterMs(retryAfter, now);
  if (hinted !== null && hinted > MAX_RETRY_AFTER_MS) return null;
  return Math.round(Math.max(backoff, hinted ?? 0) + random() * (backoff / 2));
}

function waitBeforeRetry(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

type ApiPayload<T> = (ApiEnvelope<T> & { detail?: string }) | { detail?: string };
type ApiBody<T> = { data: ApiPayload<T> | null; text: string };

async function send<T>(
  path: string,
  options: RequestOptions,
  signal: AbortSignal | null | undefined,
): Promise<{ response: Response; body: ApiBody<T> }> {
  const response = await fetch(`/api/weather${path}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...(options.headers ?? {}),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store",
    signal,
  });
  // See lib/http.ts: parsing before checking the status turns every plain-text
  // or HTML failure into a SyntaxError and hides what the server said.
  return { response, body: await readBody<ApiPayload<T>>(response) };
}

function unwrap<T>(response: Response, body: ApiBody<T>): ApiEnvelope<T> {
  if (!response.ok) throw new Error(failureMessage(response, body));
  if (!body.data) throw new Error(`응답을 해석하지 못했습니다 (${response.status})`);
  return body.data as ApiEnvelope<T>;
}

/**
 * 시간 초과(504·시도 상한) 뒤에도 다시 보내는 경로. 공개 조회(`/v1/weather/*`)는 API가 DB 마감
 * (statement_timeout)으로 버려진 조회를 스스로 끝낸다. 관리 GET은 그런 마감이 없어 BFF가 끊어도
 * API thread가 계속 돌므로, 다시 보내면 같은 느린 조회가 겹쳐 쌓인다.
 */
function timeoutRetryAllowed(path: string): boolean {
  return path.startsWith("/v1/weather/");
}

/**
 * GET은 시도마다 20초 상한을 두고 502/503/504·네트워크 오류·상한 초과를 최대 2번 다시 보낸다.
 * 504와 상한 초과는 공개 조회(`/v1/weather/*`)만 다시 보낸다(timeoutRetryAllowed).
 * 호출자 signal 취소는 대기 중에도 즉시 그 취소로 끝낸다. POST/PATCH/PUT/DELETE는 서버가
 * 이미 반영했을 수 있으므로 상한·재전송 없이 한 번만 보낸다.
 */
async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiEnvelope<T>> {
  const method = (options.method ?? "GET").toUpperCase();
  if (method !== "GET") {
    const { response, body } = await send<T>(path, options, options.signal);
    return unwrap(response, body);
  }
  const callerSignal = options.signal ?? undefined;
  const retryTimeouts = timeoutRetryAllowed(path);
  for (let retry = 0; ; retry += 1) {
    callerSignal?.throwIfAborted();
    const timeout = AbortSignal.timeout(GET_ATTEMPT_TIMEOUT_MS);
    const signal = callerSignal ? AbortSignal.any([callerSignal, timeout]) : timeout;
    let delay: number | null;
    try {
      const { response, body } = await send<T>(path, options, signal);
      const retryable = RETRYABLE_STATUSES.has(response.status) &&
        (response.status !== 504 || retryTimeouts);
      if (!retryable || retry >= GET_MAX_RETRIES) {
        return unwrap(response, body);
      }
      delay = retryDelayMs(retry, response.headers.get("retry-after"));
      if (delay === null) return unwrap(response, body);
    } catch (error) {
      if (callerSignal?.aborted) throw error;
      const timedOut = timeout.aborted;
      // fetch의 전송 실패는 TypeError다. 그 밖의 예외는 코드 결함이므로 그대로 올린다.
      if (!timedOut && !(error instanceof TypeError)) throw error;
      if (retry >= GET_MAX_RETRIES || (timedOut && !retryTimeouts)) {
        if (!timedOut) throw error;
        throw new Error(
          `날씨 서버 응답이 ${GET_ATTEMPT_TIMEOUT_MS / 1000}초 안에 오지 않았습니다. 잠시 후 다시 시도하세요.`,
        );
      }
      delay = retryDelayMs(retry, null) ?? 0;
    }
    await waitBeforeRetry(delay, callerSignal);
  }
}

export function getLocations(
  search?: string,
  limit = 100,
  offset = 0,
): Promise<ApiEnvelope<Location[]>> {
  const query = new URLSearchParams();
  if (search?.trim()) query.set("search", search.trim());
  query.set("limit", String(limit));
  query.set("offset", String(offset));
  return request<Location[]>(`/v1/admin/locations${query.size ? `?${query}` : ""}`);
}

function appendProviders(query: URLSearchParams, providers?: readonly string[]) {
  for (const provider of providers ?? []) query.append("provider", provider);
}

function appendKinds(query: URLSearchParams, kinds?: readonly WeatherKind[]) {
  for (const kind of kinds ?? []) query.append("kind", kind);
}

export function getPublicLocations(limit = 1000, offset = 0, providers?: string[], signal?: AbortSignal, weatherOnly = false, kinds?: WeatherKind[]): Promise<ApiEnvelope<Location[]>> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  appendProviders(query, providers);
  appendKinds(query, kinds);
  if (weatherOnly) query.set("weather_only", "true");
  return request<Location[]>(`/v1/weather/locations?${query}`, { signal });
}

export async function getAllPublicLocations(providers?: string[], signal?: AbortSignal, weatherOnly = false, kinds?: WeatherKind[]): Promise<Location[]> {
  if (providers?.length === 0 || kinds?.length === 0) return [];
  const pageSize = 1000;
  const all: Location[] = [];
  let offset = 0;
  while (true) {
    signal?.throwIfAborted();
    const page = await getPublicLocations(pageSize, offset, providers, signal, weatherOnly, kinds);
    all.push(...page.data);
    if (page.data.length < pageSize) return all;
    offset += page.data.length;
  }
}

export type WeatherRegionProjection = {
  ready: boolean;
  calculated_at: string | null;
  items: { level: "sido" | "sigungu"; region_key: string; label: string; member_count: number; location: Location }[];
};

export function getPublicRegionLocations(signal?: AbortSignal): Promise<ApiEnvelope<WeatherRegionProjection>> {
  return request<WeatherRegionProjection>("/v1/weather/regions", { signal });
}

export type SourceRecordSummary = {
  source_record_key: string;
  provider: string;
  dataset_key: string;
  source_entity_type: string;
  source_entity_id: string;
  raw_payload_hash: string;
  fetched_at: string;
  imported_at: string;
  row_count: number | null;
  response_metadata: Record<string, unknown>;
};

export function createLocation(
  location: Pick<Location, "location_id" | "name" | "latitude" | "longitude" | "nx" | "ny"> & {
    region_code?: string | null;
    enabled?: boolean;
    metadata?: Record<string, unknown>;
  },
): Promise<ApiEnvelope<Location>> {
  return request<Location>("/v1/admin/locations", { method: "POST", body: location });
}

export function updateLocation(
  locationId: string,
  changes: Partial<Pick<Location, "name" | "latitude" | "longitude" | "nx" | "ny" | "region_code" | "enabled" | "metadata">>,
): Promise<ApiEnvelope<Location>> {
  return request<Location>(`/v1/admin/locations/${encodeURIComponent(locationId)}`, {
    method: "PATCH",
    body: changes,
  });
}

export function getLatest(locationId: string, limit = 200, signal?: AbortSignal, providers?: string[], kinds?: WeatherKind[]): Promise<ApiEnvelope<WeatherValue[]>> {
  const query = new URLSearchParams({ limit: String(limit) });
  appendProviders(query, providers);
  appendKinds(query, kinds);
  return request<WeatherValue[]>(
    `/v1/weather/locations/${encodeURIComponent(locationId)}/latest?${query}`,
    { signal },
  );
}

/**
 * Earliest `from` a forecast preview should ask for.
 *
 * `/v1/weather/locations/{id}/forecast` is a timeline query, not an
 * upcoming-only route: called without `from` it answers from the oldest row the
 * projection still holds. Anchor at the top of the current hour, which is the
 * earliest target a reader would call a forecast.
 *
 * The boundary is taken in UTC so the result does not depend on the viewer's
 * offset; a local-time boundary would shift on half-hour offsets such as
 * UTC+05:30. Either way the anchor is at most an hour behind `now`.
 */
export function forecastWindowStart(now: Date = new Date()): string {
  const start = new Date(now);
  start.setUTCMinutes(0, 0, 0);
  return start.toISOString();
}

/**
 * End of the forecast window the location detail shows: the start of the KST day
 * after tomorrow. The API bound is inclusive; rows at exactly this instant belong
 * to the day after tomorrow and the tomorrow panel ignores them.
 *
 * The detail renders the next 24 forecast slots and tomorrow's day/night. Asking
 * for an open-ended range with a row cap returned 1000 rows that, for dense
 * locations, ended 33-43 hours out -- before tomorrow night -- while sparse
 * locations got three days nobody displays (odroid, 2026-11-10). Bound the range
 * by what is rendered and let the cap be a safety net, not the window.
 * KST has no daylight saving, so a fixed +09:00 offset is exact.
 */
export function forecastWindowEnd(now: Date = new Date()): string {
  const kstMidnight = new Date(now.getTime() + 9 * 3600_000);
  kstMidnight.setUTCHours(0, 0, 0, 0);
  return new Date(kstMidnight.getTime() - 9 * 3600_000 + 2 * 86_400_000).toISOString();
}

/**
 * Row cap for the detail window above. With a bounded window the cap is only a
 * safety net, so use the API maximum (the densest sampled location had about
 * 1,100 rows in a 35-hour window, so roughly 1,500 in a full 48 hours).
 */
export const FORECAST_DETAIL_LIMIT = 5000;

export function getForecast(
  locationId: string,
  from?: string,
  to?: string,
  limit = 200,
  signal?: AbortSignal,
  providers?: string[],
  kinds?: WeatherKind[],
): Promise<ApiEnvelope<WeatherValue[]>> {
  const query = new URLSearchParams({ limit: String(limit) });
  appendProviders(query, providers);
  appendKinds(query, kinds);
  if (from) query.set("from", from);
  if (to) query.set("to", to);
  return request<WeatherValue[]>(
    `/v1/weather/locations/${encodeURIComponent(locationId)}/forecast?${query.toString()}`,
    { signal },
  );
}

export function getNearby(
  latitude: number,
  longitude: number,
  radiusKm = 500,
  limit = 100,
): Promise<ApiEnvelope<NearbyWeather[]>> {
  const query = new URLSearchParams({
    lat: String(latitude),
    lon: String(longitude),
    radius_km: String(radiusKm),
    limit: String(limit),
  });
  return request<NearbyWeather[]>(`/v1/weather/nearby?${query}`);
}

export function getWeatherResolve(
  latitude: number,
  longitude: number,
  radiusKm = 500,
): Promise<ApiEnvelope<ResolvedWeather>> {
  const query = new URLSearchParams({
    lat: String(latitude),
    lon: String(longitude),
    radius_km: String(radiusKm),
  });
  return request<ResolvedWeather>(`/v1/weather/resolve?${query}`);
}

export function getMarkerSummaries(
  locationIds: string[],
  signal?: AbortSignal,
  providers?: string[],
  kinds?: WeatherKind[],
): Promise<ApiEnvelope<WeatherMarker[]>> {
  const query = new URLSearchParams();
  appendProviders(query, providers);
  appendKinds(query, kinds);
  for (const locationId of locationIds) query.append("location_id", locationId);
  return request<WeatherMarker[]>(`/v1/weather/markers?${query.toString()}`, { signal });
}

export function getSyncRuns(limit = 50): Promise<ApiEnvelope<SyncRun[]>> {
  return request<SyncRun[]>(`/v1/admin/sync-runs?limit=${limit}`);
}

export function getSyncRunSources(
  runId: string,
): Promise<ApiEnvelope<SourceRecordSummary[]>> {
  return request<SourceRecordSummary[]>(
    `/v1/admin/sync-runs/${encodeURIComponent(runId)}/sources`,
  );
}

export function getProviders(): Promise<ApiEnvelope<Provider[]>> {
  return request<Provider[]>("/v1/admin/providers");
}

export type ProviderCredential = {
  provider: string;
  configured: boolean;
  source: "database" | "environment" | "none";
  fingerprint: string | null;
  last4: string | null;
  updated_at: string | null;
};

export function getProviderCredentials(): Promise<ApiEnvelope<ProviderCredential[]>> {
  return request<ProviderCredential[]>("/v1/admin/provider-credentials");
}

export function updateProviderCredential(
  provider: string,
  apiKey: string,
): Promise<ApiEnvelope<ProviderCredential>> {
  return request<ProviderCredential>(
    `/v1/admin/provider-credentials/${encodeURIComponent(provider)}`,
    { method: "PUT", body: { api_key: apiKey } },
  );
}

export function deleteProviderCredential(
  provider: string,
): Promise<ApiEnvelope<ProviderCredential>> {
  return request<ProviderCredential>(
    `/v1/admin/provider-credentials/${encodeURIComponent(provider)}`,
    { method: "DELETE" },
  );
}

export function getHealth(): Promise<{ status: string; service: string; version: string | null }> {
  return fetch("/api/weather/health", { cache: "no-store" }).then(async (response) => {
    if (!response.ok) throw new Error(`API 연결 실패 (${response.status})`);
    return (await response.json()) as { status: string; service: string; version: string | null };
  });
}


export type AddressItem = {
  target_key: string; name?: string; status: string; stale: boolean;
  address?: { full?: string } | null; region?: { legal_code?: string; admin_code?: string } | null;
  provider?: string | null; resolved_at?: string | null; next_refresh_at?: string | null; error_code?: string | null;
};
export type AddressRefreshSummary = {
  total: number; due: number; failed: number; refresh_months: number;
  configured: boolean; auto_enabled: boolean; in_progress: boolean; manual_cycle: boolean;
  last_resolved_at: string | null; items: AddressItem[];
};

export function getRegionalAlertEvents(
  bjdCode: string, since?: string, signal?: AbortSignal,
): Promise<ApiEnvelope<RegionalAlertEvents>> {
  const query = new URLSearchParams({ bjd_code: bjdCode });
  if (since) query.set("since", since);
  return request<RegionalAlertEvents>(`/v1/weather/alert-events?${query}`, { signal });
}
