export type PageMeta = {
  limit: number;
  offset: number;
  returned: number;
  total: number | null;
};

export type ApiEnvelope<T> = {
  data: T;
  meta: {
    request_id: string;
    generated_at: string;
    duration_ms: number;
    page?: PageMeta | null;
  };
};

export type Location = {
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
  distance_km: number;
  measurement_point: MeasurementPoint | null;
  latest: WeatherValue[];
  forecast: WeatherValue[];
  alerts: WeatherValue[];
};

export type ResolvedWeather = {
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
  location_id: string;
  measurement_point: MeasurementPoint | null;
  latest: WeatherValue[];
  alerts: WeatherValue[];
};

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

async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiEnvelope<T>> {
  const response = await fetch(`/api/weather${path}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...(options.headers ?? {}),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store",
  });
  const payload = (await response.json()) as ApiEnvelope<T> | { detail?: string };
  if (!response.ok) {
    const detail = "detail" in payload && payload.detail ? payload.detail : `요청 실패 (${response.status})`;
    throw new Error(detail);
  }
  return payload as ApiEnvelope<T>;
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

export function getPublicLocations(limit = 1000, offset = 0): Promise<ApiEnvelope<Location[]>> {
  return request<Location[]>(`/v1/weather/locations?limit=${limit}&offset=${offset}`);
}

export async function getAllPublicLocations(): Promise<Location[]> {
  const pageSize = 1000;
  const all: Location[] = [];
  let offset = 0;
  while (true) {
    const page = await getPublicLocations(pageSize, offset);
    all.push(...page.data);
    if (page.data.length < pageSize) return all;
    offset += page.data.length;
  }
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

export function getLatest(locationId: string, limit = 200): Promise<ApiEnvelope<WeatherValue[]>> {
  return request<WeatherValue[]>(
    `/v1/weather/locations/${encodeURIComponent(locationId)}/latest?limit=${limit}`,
  );
}

export function getForecast(
  locationId: string,
  from?: string,
  to?: string,
  limit = 200,
): Promise<ApiEnvelope<WeatherValue[]>> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (from) query.set("from", from);
  if (to) query.set("to", to);
  return request<WeatherValue[]>(
    `/v1/weather/locations/${encodeURIComponent(locationId)}/forecast?${query.toString()}`,
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

export function getMarkerSummaries(locationIds: string[]): Promise<ApiEnvelope<WeatherMarker[]>> {
  const query = new URLSearchParams();
  for (const locationId of locationIds) query.append("location_id", locationId);
  return request<WeatherMarker[]>(`/v1/weather/markers?${query.toString()}`);
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
