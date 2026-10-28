"use client";

import { Bell, CalendarDays, Cloud, Crosshair, Eye, RefreshCw, Thermometer, Timer, Wind } from "lucide-react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import { MapDetailOverlay, MapFilterBar, MapFilterField, MapFilterPanel, MapKindFilter, MapStage, MapTabs, MapTabPanel, MapViewTabs, MapWorkspace, type MapViewportReadout } from "@kor-travel/ui/map-controls";

import {
  forecastWindowStart,
  getAllPublicLocations,
  getForecast,
  getLatest,
  getMarkerSummaries,
  getProviders,
  Location,
  WeatherMarker,
  WeatherKind,
  WeatherValue,
} from "@/lib/api";
import {
  VWorldMapView,
  VWorldWeatherClusters,
} from "@/components/vworld-map-view";
import type { WeatherCondition } from "@/lib/weather-clusters";

import { SourceMultiSelect, type SourceOption } from "@/components/source-multi-select";
import { InfoButton } from "@/components/info-button";

type WeatherMapProps = {
  locations: Location[];
  catalogLoading?: boolean;
  catalogError?: string | null;
  onRetryCatalog?: () => void;
};

function metricDisplayUnit(unit: string | null) {
  return unit === "deg_c" || unit === "°C" || unit === "℃" ? "℃" : unit ?? "";
}

function metricDisplayValue(value: WeatherValue) {
  const number = value.value_number;
  if (number == null) return value.value_text ?? "—";
  if (Number.isFinite(number) && ["deg_c", "°C", "℃", "m/s"].includes(value.unit ?? "")) {
    const compact = number.toLocaleString("ko-KR", { maximumFractionDigits: 1, useGrouping: false });
    // 작은 비영 값을 0으로 바꾸지 않고, 강수량 등 다른 단위는 원래 정밀도를 유지한다.
    return Number(compact) === 0 && number !== 0 ? String(number) : compact;
  }
  return String(number);
}

function valueFor(values: WeatherValue[], ...keys: string[]) {
  const found = values.find((value) => keys.includes(value.metric_key));
  if (!found) return "—";
  const unit = metricDisplayUnit(found.unit);
  return `${metricDisplayValue(found)}${unit ? ` ${unit}` : ""}`;
}

function forecastGroups(values: WeatherValue[]) {
  const groups = new Map<string, WeatherValue[]>();
  for (const value of values) {
    const target = value.target_at;
    const group = groups.get(target) ?? [];
    group.push(value);
    groups.set(target, group);
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .slice(0, 6);
}

type MarkerState = {
  kind: "clear" | "cloud" | "rain" | "snow" | "storm" | "alert" | "unknown";
  glyph: string;
  label: string;
};

function numericCode(value: WeatherValue): number | null {
  const candidate = value.value_number ?? (value.value_text ? Number(value.value_text) : NaN);
  return Number.isFinite(candidate) ? candidate : null;
}

function classifyProviderCode(provider: string, value: WeatherValue): MarkerState["kind"] | null {
  const rawText = (value.value_text ?? value.metric_name ?? "").trim().toLocaleLowerCase();
  const code = numericCode(value);
  const source = provider.toLocaleLowerCase();

  if ((source === "visual_crossing" && !code)
    || ((source === "python-kma-api" || source === "kma") && code === null)) {
    if (/thunder|storm|번개|뇌우/.test(rawText)) return "storm";
    if (/snow|sleet|ice|눈|진눈깨비/.test(rawText)) return "snow";
    if (/rain|drizzle|shower|비|소나기/.test(rawText)) return "rain";
    if (/cloud|overcast|fog|안개|구름/.test(rawText)) return "cloud";
    if (/clear|sunny|맑음|쾌청/.test(rawText)) return "clear";
  }
  if (code === null) return null;

  if (source === "openweathermap" || source === "weatherbit") {
    if (code >= 200 && code < 300) return "storm";
    if (code >= 300 && code < 600) return "rain";
    if (code >= 600 && code < 700) return "snow";
    if (code >= 700 && code < 800) return "cloud";
    if (code === 800) return "clear";
    if (code > 800 && code <= 804) return "cloud";
  }
  if (source === "weatherapi") {
    if ([1000].includes(code)) return "clear";
    if ([1003, 1006, 1009, 1030, 1135, 1147].includes(code)) return "cloud";
    if ([1087, 1273, 1276, 1279, 1282].includes(code)) return "storm";
    if ([1066, 1114, 1117, 1210, 1214, 1218, 1222, 1225, 1255, 1258].includes(code)) return "snow";
    if (code >= 1063 && code <= 1201 || code >= 1240 && code <= 1246 || code >= 1150 && code <= 1168) return "rain";
  }
  if (source === "tomorrow_io") {
    if ([1000, 1100].includes(code)) return "clear";
    if ([1001, 1101, 1102, 1002].includes(code)) return "cloud";
    if (code >= 4000 && code <= 4201) return "rain";
    if (code >= 5000 && code <= 5100) return "snow";
    if (code === 8000) return "storm";
  }
  if (source === "accuweather") {
    if (code >= 1 && code <= 5 || code >= 30 && code <= 35) return "clear";
    if (code >= 6 && code <= 11 || code >= 36 && code <= 38) return "cloud";
    if (code >= 12 && code <= 18 || code >= 39 && code <= 47) return "rain";
    if (code >= 19 && code <= 29 || code >= 43 && code <= 44) return "snow";
  }
  if (source === "weatherstack" || source === "wttr_in") {
    if (code === 113) return "clear";
    if ([116, 119, 122].includes(code)) return "cloud";
    if (code >= 176 && code <= 359) return "rain";
    if (code >= 368 && code <= 395) return "snow";
  }
  // KMA/Open-Meteo WMO weather codes.
  if ([95, 96, 99].includes(code)) return "storm";
  if (code >= 71 && code <= 86) return "snow";
  if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) return "rain";
  if (code === 0 || code === 1) return "clear";
  if (code >= 2 && code <= 48) return "cloud";
  return null;
}

// Keep repeated location_id query strings below common proxy request-line
// limits.  The API accepts up to 500 ids, but a 500-id URL can exceed an
// nginx/HAProxy URI limit once station ids are included.  Sixty IDs keeps the
// URL small while avoiding unnecessary request waves on a nationwide catalog.
const MARKER_BATCH_SIZE = 60;
const MARKER_BATCH_CONCURRENCY = 2;

type MarkerQueue = {
  cancelled: boolean;
  controller: AbortController;
  ids: string[];
  pending: Set<string>;
  failed: Set<string>;
  active: number;
  retryToken: number;
  pump: () => void;
};

type MapViewport = {
  west: number;
  east: number;
  south: number;
  north: number;
};

function viewportFromMap(instance: MapLibreMap): MapViewport | null {
  try {
    const bounds = instance.getBounds();
    return {
      west: bounds.getWest(),
      east: bounds.getEast(),
      south: bounds.getSouth(),
      north: bounds.getNorth(),
    };
  } catch {
    return null;
  }
}

function isInViewport(location: Location, viewport: MapViewport) {
  // Korea does not cross the antimeridian, so the ordinary longitude
  // comparison is sufficient and avoids allocating MapLibre bounds for every
  // render.  A small margin prevents markers flickering at the edge while a
  // user pans the map.
  const longitudeMargin = Math.max(0.15, (viewport.east - viewport.west) * 0.08);
  const latitudeMargin = Math.max(0.1, (viewport.north - viewport.south) * 0.08);
  return (
    location.longitude >= viewport.west - longitudeMargin &&
    location.longitude <= viewport.east + longitudeMargin &&
    location.latitude >= viewport.south - latitudeMargin &&
    location.latitude <= viewport.north + latitudeMargin
  );
}

function markerValues(summary: WeatherMarker): WeatherValue[] {
  // 현재값이 있으면 미래 예보의 강수·온도를 섞지 않는다.
  if (summary.latest.length) return summary.latest;
  const forecast = summary.forecast ?? [];
  const next = Math.min(...forecast.map((row) => Date.parse(row.target_at)).filter(Number.isFinite));
  return forecast.filter((row) => Date.parse(row.target_at) === next);
}

function markerState(summary: WeatherMarker | undefined): MarkerState {
  if (!summary) return { kind: "unknown", glyph: "·", label: "날씨 정보 없음" };
  if (summary.alerts.length) {
    return { kind: "alert", glyph: "!", label: `특보 ${summary.alerts.length}건` };
  }
  const rows = markerValues(summary);
  const codeRows = rows.filter((row) =>
    ["WEATHER_CODE", "weather_code", "SKY", "PTY"].includes(row.metric_key),
  );
  const precipitation = codeRows.find((row) => row.metric_key === "PTY" && (numericCode(row) ?? 0) > 0);
  if (precipitation) {
    const code = numericCode(precipitation);
    const snow = code === 3 || code === 7;
    return { kind: snow ? "snow" : "rain", glyph: snow ? "❄" : "☂", label: snow ? "눈" : "강수" };
  }
  for (const codeRow of codeRows.filter((row) => row.metric_key !== "PTY")) {
    const kind = classifyProviderCode(codeRow.provider, codeRow);
    if (kind) {
      const glyph = kind === "storm" ? "⚡" : kind === "snow" ? "❄" : kind === "rain" ? "☂" : kind === "clear" ? "☀" : "☁";
      const label = kind === "storm" ? "뇌우" : kind === "snow" ? "눈" : kind === "rain" ? "비" : kind === "clear" ? "맑음" : "구름";
      return { kind, glyph, label };
    }
  }
  return { kind: "unknown", glyph: "·", label: "날씨 정보 없음" };
}

function markerCondition(kind: MarkerState["kind"]): WeatherCondition {
  if (kind === "clear") return "sunny";
  if (kind === "rain") return "rainy";
  if (kind === "snow") return "snowy";
  if (kind === "storm" || kind === "alert") return "storm";
  if (kind === "cloud") return "cloudy";
  return "unknown";
}

function markerTemperature(summary: WeatherMarker | undefined): number | null {
  if (!summary) return null;
  const row = markerValues(summary).find((value) =>
    ["T1H", "TMP", "TEMP", "temperature", "temperature_2m", "temp_c"].includes(value.metric_key),
  );
  if (!row) return null;
  const numeric = row.value_number ?? (row.value_text?.trim() ? Number(row.value_text) : NaN);
  return Number.isFinite(numeric) ? numeric : null;
}

const WEATHER_KINDS = [
  { value: "alert", label: "특보", icon: <Bell aria-hidden="true" /> },
  { value: "nowcast", label: "실황", icon: <Cloud aria-hidden="true" /> },
  { value: "ultra_short", label: "초단기", icon: <Timer aria-hidden="true" /> },
  { value: "short", label: "단기", icon: <CalendarDays aria-hidden="true" /> },
  { value: "mid", label: "중기", icon: <CalendarDays aria-hidden="true" /> },
  { value: "observed", label: "관측", icon: <Eye aria-hidden="true" /> },
] satisfies { value: WeatherKind; label: string; icon: React.ReactNode }[];

// 현재 값(관측·실황·특보)을 가진 종류. 예보 종류만 고르면 latest의 지난 예보 대체값을
// '최근 측정값'으로 보이지 않도록 현재 값 조회 자체를 하지 않는다.
const CURRENT_KINDS: WeatherKind[] = ["alert", "nowcast", "observed"];

function includesCurrentKind(kinds: WeatherKind[] | undefined) {
  return !kinds || kinds.some((kind) => CURRENT_KINDS.includes(kind));
}

const DEFAULT_CENTER: [number, number] = [127.8, 36.2];
const SELECTED_ZOOM = 7.2;

// 필터 결과를 불러오는 동안(목록이 잠시 빈 동안)은 보던 위치를 이전 목록에서 그대로 쓴다.
// 그래야 선택 ID가 id → 없음 → id로 흔들려 카메라가 되돌아가거나 상세를 다시 부르지 않는다.
function resolveSelection<T extends { location_id: string }>(
  visible: T[], selectedId: string, filterLoading: boolean, held: T[][],
): T | undefined {
  const match = visible.find((location) => location.location_id === selectedId);
  if (match) return match;
  if (filterLoading && selectedId) {
    for (const catalog of held) {
      const kept = catalog.find((location) => location.location_id === selectedId);
      if (kept) return kept;
    }
  }
  return visible[0];
}

export function WeatherMap({ locations, catalogLoading = false, catalogError, onRetryCatalog }: WeatherMapProps) {
  const map = useRef<MapLibreMap | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [selectedId, setSelectedId] = useState(locations[0]?.location_id ?? "");
  const [values, setValues] = useState<WeatherValue[]>([]);
  const [forecast, setForecast] = useState<WeatherValue[]>([]);
  const [query, setQuery] = useState("");
  const [selectedSources, setSelectedSources] = useState<string[] | null>(null);
  const [mode, setMode] = useState<"map" | "list">("map");
  const [loading, setLoading] = useState(false);
  const [forecastLoading, setForecastLoading] = useState(false);
  const [forecastMessage, setForecastMessage] = useState("");
  const [message, setMessage] = useState("");
  const [mapError, setMapError] = useState("");
  const [markerError, setMarkerError] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [markerRetryToken, setMarkerRetryToken] = useState(0);
  const markerQueueRef = useRef<MarkerQueue | null>(null);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null);
  const [summaries, setSummaries] = useState<Record<string, WeatherMarker>>({});
  const summariesRef = useRef<Record<string, WeatherMarker>>({});
  const markerRefreshRef = useRef("");
  const [viewport, setViewport] = useState<MapViewport | null>(null);
  const [sourceOptions, setSourceOptions] = useState<SourceOption[]>([]);
  const [sourceError, setSourceError] = useState("");
  const [sourceListError, setSourceListError] = useState("");
  const [sourceCatalog, setSourceCatalog] = useState<{ key: string; locations: Location[] } | null>(null);
  const [selectedKinds, setSelectedKinds] = useState<WeatherKind[]>(WEATHER_KINDS.map((kind) => kind.value));
  const kindFilter = useMemo(() => selectedKinds.length === WEATHER_KINDS.length ? undefined : selectedKinds, [selectedKinds]);
  const [mapViewport, setMapViewport] = useState<MapViewportReadout | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const detailRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const filterTriggerRef = useRef<HTMLButtonElement>(null);
  const providerFilter = selectedSources ?? undefined;
  const sourceKey = JSON.stringify([selectedSources, kindFilter]);
  const needsSourceCatalog = selectedSources !== null || kindFilter !== undefined;
  const [datasetLabels, setDatasetLabels] = useState<Map<string, string>>(new Map());
  const [providerLabels, setProviderLabels] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    getProviders()
      .then((result) => {
        const datasets = new Map<string, string>();
        const providers = new Map<string, string>();
        for (const provider of result.data) {
          providers.set(provider.provider, provider.label);
          for (const dataset of provider.datasets) datasets.set(dataset.key, dataset.label);
        }
        setSourceOptions(result.data.map((provider) => ({ value: provider.provider, label: provider.label })));
        setDatasetLabels(datasets);
        setProviderLabels(providers);
      })
      .catch(() => setSourceListError("소스 목록을 불러오지 못했습니다. 페이지를 새로고침해 주세요."));
  }, []);

  useEffect(() => {
    setSourceError("");
    if (!needsSourceCatalog || selectedSources?.length === 0 || selectedKinds.length === 0) return;
    const controller = new AbortController();
    void getAllPublicLocations(selectedSources ?? undefined, controller.signal, false, kindFilter)
      .then((next) => {
        if (!controller.signal.aborted) setSourceCatalog({ key: sourceKey, locations: next });
      })
      .catch(() => {
        if (!controller.signal.aborted) setSourceError("선택한 소스의 위치를 불러오지 못했습니다. 다시 선택해 주세요.");
      });
    return () => controller.abort();
  }, [selectedSources, sourceKey, kindFilter, selectedKinds.length, needsSourceCatalog]);
  const sourceLoading = needsSourceCatalog && selectedSources?.length !== 0 && selectedKinds.length > 0
    && sourceCatalog?.key !== sourceKey && !sourceError;
  const visibleLocations = useMemo(() => {
    const catalog = !needsSourceCatalog ? locations
      : (selectedSources?.length === 0 || selectedKinds.length === 0) ? [] : sourceCatalog?.key === sourceKey ? sourceCatalog.locations : [];
    const normalized = query.trim().toLocaleLowerCase();
    return normalized ? catalog.filter((location) =>
      `${location.name} ${location.location_id} ${location.region_code ?? ""}`.toLocaleLowerCase().includes(normalized),
    ) : catalog;
  }, [locations, query, selectedSources, selectedKinds.length, sourceCatalog, sourceKey, needsSourceCatalog]);
  const selected = resolveSelection(
    visibleLocations, selectedId, catalogLoading || sourceLoading, [sourceCatalog?.locations ?? [], locations],
  );
  const selectedLocationId = selected?.location_id;
  // VWorldMapView treats a changed center/zoom prop as an explicit recenter
  // request.  Give it only the first camera and let the selection effect below
  // be the single recenter path, so a filter-driven fallback selection or a new
  // catalog object for the same location never moves the user's camera.
  const [initialCamera] = useState(() => locations[0]
    ? { center: [locations[0].longitude, locations[0].latitude] as [number, number], zoom: SELECTED_ZOOM }
    : { center: DEFAULT_CENTER, zoom: 6 });
  // 카탈로그 도착 전의 기본 카메라였으면 첫 선택 이동에서 확대도 함께 한다(이전 zoom prop 동작).
  const zoomOnFirstMoveRef = useRef(initialCamera.zoom < SELECTED_ZOOM);
  const automaticSelectionRef = useRef<string | null>(null);
  const selectedPositionRef = useRef<[number, number] | null>(null);
  const mapLocations = useMemo(() => {
    if (!viewport) return selected ? [selected] : [];
    const inViewport = visibleLocations.filter((location) => isInViewport(location, viewport));
    if (!selected || inViewport.some((location) => location.location_id === selected.location_id)) {
      return inViewport;
    }
    return [selected, ...inViewport];
  }, [selected, viewport, visibleLocations]);
  // 상세·제공처 상태만 바뀐 렌더는 동일한 GeoJSON을 다시 만들지 않는다.
  const markers = useMemo(() => mapLocations.map((location) => {
    const summary = markerRefreshRef.current === JSON.stringify([refreshToken, providerFilter, kindFilter]) ? summaries[location.location_id] : undefined;
    const state = markerState(summary);
    return {
      id: location.location_id,
      lngLat: [location.longitude, location.latitude] as [number, number],
      temperature: markerTemperature(summary),
      condition: markerCondition(state.kind),
      alertCount: summary?.alerts.length ?? 0,
      selected: location.location_id === selectedId,
      ariaLabel: `${location.name}: ${state.label} 날씨 보기`,
      title: location.name,
      onClick: () => { setSelectedId(location.location_id); setDetailOpen(true); requestAnimationFrame(() => detailRef.current?.focus()); },
    };
  }), [mapLocations, selectedId, summaries, providerFilter, kindFilter, refreshToken]);
  const selectedSummary = markerRefreshRef.current === JSON.stringify([refreshToken, providerFilter, kindFilter]) && selected
    ? summaries[selected.location_id] : undefined;
  const groupedForecast = useMemo(() => forecastGroups(forecast), [forecast]);
  const latestCollectedAt = useMemo(() => {
    const timestamps = values
      .map((value) => Date.parse(value.collected_at))
      .filter((timestamp) => Number.isFinite(timestamp));
    return timestamps.length ? new Date(Math.max(...timestamps)) : null;
  }, [values]);
  const isFresh = latestCollectedAt !== null && Date.now() - latestCollectedAt.getTime() <= 2 * 60 * 60 * 1000;

  function closeDetail() {
    setDetailOpen(false);
    const target = searchRef.current?.getClientRects().length ? searchRef.current
      : filterTriggerRef.current?.getClientRects().length ? filterTriggerRef.current
      : map.current?.getCanvas();
    target?.focus({ preventScroll: true });
  }

  useEffect(() => {
    // 필터 결과를 불러오는 동안의 빈 목록은 선택·상세를 지우지 않는다.
    if (catalogLoading || sourceLoading) return;
    if (!visibleLocations.length) {
      if (selectedId) setSelectedId("");
      setDetailOpen(false);
      return;
    }
    if (!visibleLocations.some((location) => location.location_id === selectedId)) {
      // 보던 위치가 필터에서 빠지면 대체 위치를 고르되 상세는 닫고 카메라는 그대로 둔다.
      // 처음 고르는 위치(selectedId 없음)는 명시 선택처럼 카메라를 옮긴다.
      automaticSelectionRef.current = selectedId ? visibleLocations[0].location_id : null;
      setSelectedId(visibleLocations[0].location_id);
      setDetailOpen(false);
    }
  }, [catalogLoading, selectedId, sourceLoading, visibleLocations]);

  useEffect(() => {
    selectedPositionRef.current = selected ? [selected.longitude, selected.latitude] : null;
  });

  useEffect(() => {
    // 위치 ID가 바뀔 때만 옮긴다. 같은 위치의 새 catalog 객체에는 반응하지 않는다.
    const automatic = automaticSelectionRef.current === selectedLocationId;
    automaticSelectionRef.current = null;
    const position = selectedPositionRef.current;
    if (automatic || !map.current || !mapReady || !position) return;
    const zoom = zoomOnFirstMoveRef.current ? { zoom: SELECTED_ZOOM } : {};
    zoomOnFirstMoveRef.current = false;
    map.current.easeTo({ center: position, duration: 500, ...zoom });
  }, [mapReady, selectedLocationId]);

  function syncViewport(instance: MapLibreMap) {
    const center = instance.getCenter();
    setMapViewport({ longitude: center.lng, latitude: center.lat, zoom: instance.getZoom() });
    const next = viewportFromMap(instance);
    if (!next) return;
    setViewport((previous) => {
      if (
        previous &&
        Math.abs(previous.west - next.west) < 0.00001 &&
        Math.abs(previous.east - next.east) < 0.00001 &&
        Math.abs(previous.south - next.south) < 0.00001 &&
        Math.abs(previous.north - next.north) < 0.00001
      ) {
        return previous;
      }
      return next;
    });
  }

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    // 위치가 바뀌면 이전 값·오류와 진행 중 요청을 함께 격리한다.
    setValues([]);
    setForecast([]);
    setMessage("");
    setForecastMessage("");
    setLastRefreshedAt(null);
    const current = includesCurrentKind(kindFilter);
    setLoading(detailOpen && Boolean(selectedLocationId) && current);
    setForecastLoading(detailOpen && Boolean(selectedLocationId));
    if (!detailOpen || !selectedLocationId) return () => controller.abort();

    if (!current) {
      // 예보 종류만 고르면 latest가 지난 예보를 대신 돌려준다. 측정값으로 보이지 않는다.
      setMessage("선택한 종류에는 현재 값이 없습니다.");
    } else {
      void getLatest(selectedLocationId, 200, signal, providerFilter, kindFilter)
        .then((latest) => {
          if (signal.aborted) return;
          setValues(latest.data);
          setLastRefreshedAt(new Date());
          if (!latest.data.length) setMessage("아직 수집된 최신 값이 없습니다.");
        })
        .catch((reason: unknown) => {
          if (!signal.aborted) setMessage(reason instanceof Error ? reason.message : "날씨를 불러오지 못했습니다.");
        })
        .finally(() => {
          if (!signal.aborted) setLoading(false);
        });
    }
    // 예보가 늦거나 실패해도 먼저 도착한 현재 날씨는 즉시 표시한다.
    void getForecast(selectedLocationId, forecastWindowStart(), undefined, 100, signal, providerFilter, kindFilter)
      .then((next) => {
        if (!signal.aborted) setForecast(next.data);
      })
      .catch((reason: unknown) => {
        if (!signal.aborted) setForecastMessage(reason instanceof Error ? reason.message : "예보를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (!signal.aborted) setForecastLoading(false);
      });
    return () => controller.abort();
  }, [detailOpen, refreshToken, selectedLocationId, providerFilter, kindFilter]);

  useEffect(() => {
    if (mode !== "map") return;
    const cacheKey = JSON.stringify([refreshToken, providerFilter, kindFilter]);
    if (markerRefreshRef.current !== cacheKey) {
      markerRefreshRef.current = cacheKey;
      summariesRef.current = {};
      setSummaries({});
    }
    const queue: MarkerQueue = {
      cancelled: false, controller: new AbortController(), ids: [], pending: new Set(),
      failed: new Set(), active: 0, retryToken: -1, pump: () => {},
    };
    queue.pump = () => {
      if (queue.cancelled) return;
      setMarkerError(queue.ids.some((id) => queue.failed.has(id))
        ? "일부 위치의 날씨를 불러오지 못했습니다." : "");
      while (queue.active < MARKER_BATCH_CONCURRENCY) {
        const chunk = queue.ids.filter((id) => !summariesRef.current[id]
          && !queue.pending.has(id) && !queue.failed.has(id)).slice(0, MARKER_BATCH_SIZE);
        if (!chunk.length) break;
        chunk.forEach((id) => queue.pending.add(id));
        queue.active += 1;
        void getMarkerSummaries(chunk, queue.controller.signal, providerFilter,
          kindFilter ?? WEATHER_KINDS.map((kind) => kind.value))
          .then((response) => {
            if (queue.cancelled) return;
            for (const item of response.data) summariesRef.current[item.location_id] = item;
            // 빈/부분 응답도 자동 재요청 루프로 만들지 않는다. 수동 재조회 때 다시 허용한다.
            for (const id of chunk) if (!summariesRef.current[id]) queue.failed.add(id);
            setSummaries({ ...summariesRef.current });
          })
          .catch(() => {
            if (!queue.cancelled) chunk.forEach((id) => queue.failed.add(id));
          })
          .finally(() => {
            chunk.forEach((id) => queue.pending.delete(id));
            queue.active -= 1;
            queue.pump();
          });
      }
    };
    markerQueueRef.current = queue;
    return () => {
      queue.cancelled = true;
      queue.controller.abort();
      if (markerQueueRef.current === queue) markerQueueRef.current = null;
    };
  }, [mode, refreshToken, providerFilter, kindFilter]);

  useEffect(() => {
    const queue = markerQueueRef.current;
    if (!queue || mode !== "map") return;
    // 같은 필터 범위에서는 지도 이동 중인 batch를 이어받는다. 다음 배정만 현재 범위로 좁힌다.
    queue.ids = mapLocations.map((location) => location.location_id);
    if (queue.retryToken !== markerRetryToken) {
      queue.retryToken = markerRetryToken;
      for (const id of queue.ids) queue.failed.delete(id);
    }
    queue.pump();
  }, [mapLocations, mode, refreshToken, providerFilter, kindFilter, markerRetryToken]);

  useEffect(() => {
    if (mode !== "map" || !map.current) return;
    requestAnimationFrame(() => map.current?.resize());
  }, [mode]);

  const filterLabel = ["지도 필터", selectedKinds.length === WEATHER_KINDS.length ? null : selectedKinds.length === 1 ? WEATHER_KINDS.find((kind) => kind.value === selectedKinds[0])?.label : `종류 ${selectedKinds.length}개`, selectedSources === null ? null : selectedSources.length === 1 ? providerLabels.get(selectedSources[0]) ?? selectedSources[0] : `출처 ${selectedSources.length}개`, query.trim() ? "검색 적용" : null].filter(Boolean).join(" · ");
  const visibleMapError = mode === "map" ? mapError : "";
  const visibleMarkerError = mode === "map" && mapLocations.length > 0 ? markerError : "";
  const mapNotice = catalogLoading || catalogError || sourceLoading || sourceError || sourceListError || visibleMapError || visibleMarkerError || !visibleLocations.length ? (
            <div className="weather-map-notice" role={catalogError || sourceError || sourceListError || visibleMapError || visibleMarkerError ? "alert" : "status"} aria-live="polite">
              {catalogLoading ? <span>위치 카탈로그를 불러오는 중…</span> : catalogError ? <><span>{catalogError}</span>{onRetryCatalog ? <button type="button" className="secondary" onClick={onRetryCatalog}>다시 시도</button> : null}</> : !locations.length && !visibleLocations.length && !sourceLoading ? <span>활성화된 위치가 없습니다.</span> : !sourceLoading && !sourceError && !visibleLocations.length ? <span>검색 결과가 없습니다.</span> : null}
              {sourceError ? <span>{sourceError}</span> : null}
              {sourceListError ? <span>{sourceListError}</span> : null}
              {!catalogLoading && sourceLoading ? <span>선택 조건의 위치 조회 중…</span> : null}
              {visibleMapError ? <span>{visibleMapError}</span> : null}
              {visibleMarkerError ? <><span>{visibleMarkerError}</span><button type="button" className="secondary" onClick={() => setMarkerRetryToken((value) => value + 1)}>날씨 다시 조회</button></> : null}
            </div>
          ) : null;
  const detailPanel = selected && detailOpen ? (
    <MapDetailOverlay key={selected.location_id} ref={detailRef} className="weather-inspector" aria-label="선택한 위치 상세" onDismiss={closeDetail} collapsible header={
              <div className="inspector-heading">
                <div>
                  <div className="inspector-title"><h2>{selected.name}</h2><InfoButton label="위치·데이터 출처">
                    <dl className="info-metadata">
                      <div><dt>위치 ID</dt><dd>{selected.location_id}</dd></div>
                      <div><dt>좌표</dt><dd>{selected.latitude.toFixed(4)}, {selected.longitude.toFixed(4)}</dd></div>
                      {lastRefreshedAt ? <div><dt>조회 시각</dt><dd>{lastRefreshedAt.toLocaleString("ko-KR")}</dd></div> : null}
                    </dl>
                    {values.length ? <div className="info-source-list">
                      <strong>최근 측정값 출처</strong>
                      <ul>{Array.from(new Map(values.map((value) => [`${value.provider}/${value.dataset_key}`, value])).values()).map((value) => (
                        <li key={`${value.provider}/${value.dataset_key}`}>{providerLabels.get(value.provider) ?? value.provider} · {datasetLabels.get(value.dataset_key) ?? value.dataset_key}</li>
                      ))}</ul>
                      <details><summary>측정값별 원본 키</summary><dl className="info-metadata">{values.map((value) => <div key={value.value_id}><dt>{value.metric_name ?? value.metric_key}</dt><dd>{value.source_record_key || "원본 키 없음"}</dd></div>)}</dl></details>
                    </div> : null}
                  </InfoButton></div>
                </div>
              </div>
    }>
              {includesCurrentKind(kindFilter) ? <div className="inspector-freshness weather-current-status"><span className={`status ${isFresh ? "on" : "warn"}`}>{isFresh ? "최신" : "오래됨"}</span><small>{latestCollectedAt ? `수집 ${latestCollectedAt.toLocaleString("ko-KR")}` : "수집 시각 없음"}</small></div> : null}
              {loading ? <div className="loading-block" role="status" aria-live="polite">날씨 조회 중…</div> : null}
                {message ? <div className="empty" role="status">{message}</div> : null}
                {selectedSummary?.alerts.length ? (
                  <div className="alert-panel" role="status">
                    <strong>기상특보</strong>
                    {selectedSummary.alerts.slice(0, 3).map((alert) => (
                      <span key={alert.value_id}>
                        {alert.value_text ?? alert.metric_name ?? "특보"} · {alert.severity ?? "주의"}
                      </span>
                    ))}
                  </div>
                ) : null}
                {!loading ? <>
                <div className="metric-grid"><div className="metric-card primary"><Thermometer size={17} /><span>기온</span><strong>{valueFor(values, "T1H", "TMP", "TEMP", "temperature", "temperature_2m", "temp_c")}</strong></div><div className="metric-card"><Wind size={17} /><span>풍속</span><strong>{valueFor(values, "WSD", "WIND_SPEED", "wind_speed", "wind_speed_10m", "wind_kph")}</strong></div><div className="metric-card"><span>습도</span><strong>{valueFor(values, "REH", "HUMIDITY", "relative_humidity_2m", "humidity")}</strong></div><div className="metric-card"><span>강수</span><strong>{valueFor(values, "RN1", "PCP", "PRECIP", "precipitation", "precipitation_sum", "precip_mm")}</strong></div></div>
                <div className="inspector-actions">
                  <button type="button" className="icon-button" aria-label="날씨 새로고침" title="날씨 새로고침" onClick={() => setRefreshToken((value) => value + 1)}><RefreshCw size={17} /></button>
                  <button type="button" className="icon-button" aria-label="지도에서 위치로 이동" title="지도에서 위치로 이동" onClick={() => map.current?.easeTo({ center: [selected.longitude, selected.latitude], zoom: 10, duration: 500 })}><Crosshair size={17} /></button>
                </div>
                <div className="inspector-section"><div className="section-label"><span>최근 측정값</span><span>{values.length}개</span></div><div className="metric-rows">{values.slice(0, 8).map((value) => <div key={value.value_id}><span><strong>{value.metric_name ?? value.metric_key}</strong><small>{datasetLabels.get(value.dataset_key) ?? value.dataset_key}</small></span><b>{metricDisplayValue(value)} <small>{metricDisplayUnit(value.unit)}</small></b></div>)}{!values.length ? <div className="empty">표시할 측정값이 없습니다.</div> : null}</div></div>
                </> : null}
                {selectedSummary?.measurement_point ? (
                  <div className="measurement-point">
                    <span className="section-label">측정 지점</span>
                    <strong>{selectedSummary.measurement_point?.station_name}</strong>
                    <small>
                      {selectedSummary.measurement_point?.address ?? ""} · {selectedSummary.measurement_point?.distance_km.toFixed(1)}km
                    </small>
                  </div>
                ) : null}
                <div className="inspector-section forecast-section">
                  <div className="section-label"><span>예보 미리보기</span><span>{forecast.length}개</span></div>
                  {forecastLoading ? <div className="loading-block" role="status">예보 조회 중…</div> : forecastMessage ? <div className="empty" role="status">{forecastMessage}</div> : groupedForecast.length ? (
                    <div className="forecast-list">
                      {groupedForecast.map(([targetAt, items]) => (
                        <div className="forecast-row" key={targetAt}>
                          <time dateTime={targetAt}>{new Date(targetAt).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
                          <div className="forecast-values">
                            {items.slice(0, 4).map((value) => (
                              <span key={value.value_id}>
                                <b>{value.metric_name ?? value.metric_key}</b> {metricDisplayValue(value)}{metricDisplayUnit(value.unit) ? ` ${metricDisplayUnit(value.unit)}` : ""}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : <p className="muted-note">예보 데이터가 없습니다.</p>}
                </div>
    </MapDetailOverlay>
  ) : null;

  return (
    <MapWorkspace expandable className="weather-workbench" role="region" aria-label="지도 기반 날씨 조회">
      <MapFilterPanel label={filterLabel} triggerRef={filterTriggerRef}>
      <MapFilterBar>
        <MapKindFilter options={WEATHER_KINDS} value={selectedKinds} onToggle={(value) => {
          const kind = value as WeatherKind;
          setSelectedKinds((previous) => previous.includes(kind) ? previous.filter((item) => item !== kind) : [...previous, kind]);
          setDetailOpen(false);
        }} />
        <MapFilterField label="장소 검색" htmlFor="weather-location-search">
          <input id="weather-location-search" ref={searchRef} type="search" aria-label="위치 검색" placeholder="도시·위치 검색" value={query} onChange={(event) => setQuery(event.target.value)} />
        </MapFilterField>
        <MapFilterField label="데이터 출처" htmlFor="weather-source-filter">
          <SourceMultiSelect id="weather-source-filter" options={sourceOptions} value={selectedSources} onChange={setSelectedSources} fallbackFocusRef={filterTriggerRef} />
        </MapFilterField>
      </MapFilterBar>
      </MapFilterPanel>
      <MapTabs value={mode} onValueChange={(value) => setMode(value as "map" | "list")}>
        <MapViewTabs ariaLabel="조회 방식" viewport={mapViewport} count={mode === "map" ? mapLocations.length : visibleLocations.length} />
        <MapTabPanel value="map" className="map-panel" keepMounted>
          <MapStage view="map" summary={mode === "map" ? mapNotice : null} detail={mode === "map" ? detailPanel : null}>
            <VWorldMapView
              apiKey={process.env.NEXT_PUBLIC_VWORLD_API_KEY}
              center={initialCamera.center}
              className="map-canvas"
              layerType="Base"
              onLoad={(instance) => {
                map.current = instance;
                setMapReady(true);
                syncViewport(instance);
              }}
              onMoveEnd={(instance) => syncViewport(instance)}
              onError={() => setMapError("지도 표시 오류 · 목록 보기")}
              onRecover={() => setMapError("")}
              zoom={initialCamera.zoom}
            >
              <VWorldWeatherClusters
                markers={markers}
              />
            </VWorldMapView>
          </MapStage>
        </MapTabPanel>
        <MapTabPanel value="list" className="list-panel" keepMounted>
          {mode === "list" ? mapNotice : null}
          <MapStage view="list">
            <div className="location-list map-list-overlay">
              {visibleLocations.map((location) => (
                <button aria-pressed={selectedId === location.location_id} key={location.location_id} type="button" className={selectedId === location.location_id ? "selected" : ""} onClick={() => { setSelectedId(location.location_id); setDetailOpen(true); setMode("map"); requestAnimationFrame(() => { map.current?.resize(); map.current?.jumpTo({ center: [location.longitude, location.latitude], zoom: Math.max(map.current.getZoom(), 10) }); detailRef.current?.focus(); }); }}>
                  <span className="list-pin" /><span><strong>{location.name}</strong><small>{location.location_id} · {location.latitude.toFixed(3)}, {location.longitude.toFixed(3)}</small></span><span className="list-arrow">›</span>
                </button>
              ))}
            </div>
          </MapStage>
        </MapTabPanel>
      </MapTabs>
    </MapWorkspace>
  );
}
