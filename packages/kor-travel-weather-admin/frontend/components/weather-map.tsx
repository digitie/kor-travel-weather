"use client";

import { Bell, CalendarDays, Cloud, Crosshair, Eye, RefreshCw, Thermometer, Timer, Wind, X } from "lucide-react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import { MapDetailOverlay, MapFilterBar, MapFilterField, MapKindFilter, MapTabs, MapTabPanel, MapViewTabs, type MapViewportReadout } from "@kor-travel/ui/map-controls";

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
};

function valueFor(values: WeatherValue[], ...keys: string[]) {
  const found = values.find((value) => keys.includes(value.metric_key));
  if (!found) return "—";
  return `${found.value_number ?? found.value_text ?? "—"}${found.unit ? ` ${found.unit}` : ""}`;
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
const MARKER_BATCH_CONCURRENCY = 3;

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
  return "cloudy";
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

export function WeatherMap({ locations }: WeatherMapProps) {
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
  const [refreshToken, setRefreshToken] = useState(0);
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
  const selected = visibleLocations.find((location) => location.location_id === selectedId) ?? visibleLocations[0];
  const selectedLocationId = selected?.location_id;
  // Keep the controlled map center referentially stable while the user pans.
  // VWorldMapView treats a changed center prop as an explicit recenter
  // request; creating a new array on every viewport update would therefore
  // undo a user's pan on the next render.  The selected-location effect below
  // remains the only implicit recenter trigger.
  const selectedLongitude = selected?.longitude ?? 127.8;
  const selectedLatitude = selected?.latitude ?? 36.2;
  const selectedCenter = useMemo<[number, number]>(
    () => [selectedLongitude, selectedLatitude],
    [selectedLatitude, selectedLongitude],
  );
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
    searchRef.current?.focus();
  }

  useEffect(() => {
    if (!visibleLocations.length) {
      if (selectedId) setSelectedId("");
      return;
    }
    if (!visibleLocations.some((location) => location.location_id === selectedId)) {
      setSelectedId(visibleLocations[0].location_id);
    }
  }, [selectedId, visibleLocations]);

  useEffect(() => {
    if (!map.current || !mapReady || !selected) return;
    map.current.easeTo({ center: [selected.longitude, selected.latitude], duration: 500 });
  }, [mapReady, selected]);

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
    setLoading(Boolean(selectedLocationId));
    setForecastLoading(Boolean(selectedLocationId));
    if (!selectedLocationId) return () => controller.abort();

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
  }, [refreshToken, selectedLocationId, providerFilter, kindFilter]);

  useEffect(() => {
    if (!mapLocations.length || mode !== "map") return;
    let cancelled = false;
    const controller = new AbortController();
    const cacheKey = JSON.stringify([refreshToken, providerFilter, kindFilter]);
    if (markerRefreshRef.current !== cacheKey) {
      markerRefreshRef.current = cacheKey;
      summariesRef.current = {};
      setSummaries({});
    }
    const pendingLocations = mapLocations.filter(
      (location) => !summariesRef.current[location.location_id],
    );
    if (!pendingLocations.length) return;
    const chunks: string[][] = [];
    for (let index = 0; index < pendingLocations.length; index += MARKER_BATCH_SIZE) {
      chunks.push(
        pendingLocations
          .slice(index, index + MARKER_BATCH_SIZE)
          .map((location) => location.location_id),
      );
    }
    let nextChunk = 0;
    const loadWorker = async () => {
      while (!cancelled && nextChunk < chunks.length) {
        // 다음 chunk 선택과 증가 사이에는 await가 없어 worker끼리 중복 배정하지 않는다.
        const chunk = chunks[nextChunk++];
        try {
          // 무필터 API의 기존 현재값 전용 계약과 달리 지도는 선택한 예보도 요청한다.
          const response = await getMarkerSummaries(chunk, controller.signal, providerFilter,
            kindFilter ?? WEATHER_KINDS.map((kind) => kind.value));
          if (cancelled) return;
          for (const item of response.data) summariesRef.current[item.location_id] = item;
          // 느린 다른 요청을 기다리지 않고 성공한 위치를 보존·표시한다.
          setSummaries({ ...summariesRef.current });
        } catch {
          // 실패한 chunk만 격리하고, 취소된 세대는 다음 요청도 시작하지 않는다.
          if (cancelled) return;
        }
      }
    };
    void Promise.all(
      Array.from({ length: Math.min(MARKER_BATCH_CONCURRENCY, chunks.length) }, loadWorker),
    );
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [mapLocations, mode, refreshToken, providerFilter, kindFilter]);

  useEffect(() => {
    if (mode !== "map" || !map.current) return;
    requestAnimationFrame(() => map.current?.resize());
  }, [mode]);

  return (
    <section className="weather-workbench" aria-label="지도 기반 날씨 조회">
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
          <SourceMultiSelect id="weather-source-filter" options={sourceOptions} value={selectedSources} onChange={setSelectedSources} />
        </MapFilterField>
      </MapFilterBar>

      {sourceLoading ? <p className="muted-note" role="status">소스별 위치 조회 중…</p> : null}
      {sourceError ? <p className="muted-note" role="alert">{sourceError}</p> : null}
      {sourceListError ? <p className="muted-note" role="alert">{sourceListError}</p> : null}
      <MapTabs value={mode} onValueChange={(value) => setMode(value as "map" | "list")}>
        <MapViewTabs ariaLabel="조회 방식" viewport={mapViewport} count={mode === "map" ? mapLocations.length : visibleLocations.length} status={sourceLoading ? "조회 중…" : undefined} />
        <div className="map-card kt-map-stage">
          <MapTabPanel value="map" className="map-panel" keepMounted>
            <VWorldMapView
              apiKey={process.env.NEXT_PUBLIC_VWORLD_API_KEY}
              center={selectedCenter}
              className="map-canvas"
              layerType="Base"
              onLoad={(instance) => {
                map.current = instance;
                setMapReady(true);
                syncViewport(instance);
              }}
              onMoveEnd={(instance) => syncViewport(instance)}
              onError={() => setMessage("VWorld 지도를 불러오지 못했습니다. 날씨 데이터는 계속 확인할 수 있습니다.")}
              zoom={selected ? 7.2 : 6}
            >
              <VWorldWeatherClusters
                markers={markers}
              />
            </VWorldMapView>
            <div className="map-legend"><span className="legend-dot" /> 지도 표시 위치 <span className="legend-muted">{mapLocations.length}/{visibleLocations.length}곳</span></div>
          </MapTabPanel>
          <MapTabPanel value="list" className="list-panel" keepMounted>
            <div className="location-list map-list-overlay">
              {visibleLocations.map((location) => (
                <button aria-pressed={selectedId === location.location_id} key={location.location_id} type="button" className={selectedId === location.location_id ? "selected" : ""} onClick={() => { setSelectedId(location.location_id); setDetailOpen(true); setMode("map"); requestAnimationFrame(() => { map.current?.resize(); map.current?.jumpTo({ center: [location.longitude, location.latitude], zoom: Math.max(map.current.getZoom(), 10) }); detailRef.current?.focus(); }); }}>
                  <span className="list-pin" /><span><strong>{location.name}</strong><small>{location.location_id} · {location.latitude.toFixed(3)}, {location.longitude.toFixed(3)}</small></span><span className="list-arrow">›</span>
                </button>
              ))}
              {!sourceLoading && !sourceError && !visibleLocations.length ? <div className="empty">검색 결과가 없습니다.</div> : null}
            </div>
          </MapTabPanel>
        {selected && detailOpen ? <MapDetailOverlay ref={detailRef} className="weather-inspector" aria-label="선택한 위치 상세" onDismiss={closeDetail}>
          {selected ? (
            <>
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
                  <div className="inspector-freshness"><span className={`status ${isFresh ? "on" : "warn"}`}>{isFresh ? "최신" : "오래됨"}</span><small>{latestCollectedAt ? `수집 ${latestCollectedAt.toLocaleString("ko-KR")}` : "수집 시각 없음"}</small></div>
                </div>
                <div className="inspector-actions">
                  <button type="button" className="icon-button" aria-label="상세 닫기" onClick={closeDetail}><X size={17} /></button>
                  <button type="button" className="icon-button" aria-label="날씨 새로고침" title="날씨 새로고침" onClick={() => setRefreshToken((value) => value + 1)}><RefreshCw size={17} /></button>
                  <button type="button" className="icon-button" aria-label="지도에서 위치로 이동" title="지도에서 위치로 이동" onClick={() => map.current?.easeTo({ center: [selected.longitude, selected.latitude], zoom: 10, duration: 500 })}><Crosshair size={17} /></button>
                </div>
              </div>
              {loading ? <div className="loading-block" role="status" aria-live="polite">날씨 조회 중…</div> : null}
                {message ? <div className="empty" role="status">{message}</div> : null}
                {selectedSummary?.measurement_point ? (
                  <div className="measurement-point">
                    <span className="section-label">측정 지점</span>
                    <strong>{selectedSummary.measurement_point?.station_name}</strong>
                    <small>
                      {selectedSummary.measurement_point?.address ?? ""} · {selectedSummary.measurement_point?.distance_km.toFixed(1)}km
                    </small>
                  </div>
                ) : null}
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
                <div className="inspector-section"><div className="section-label"><span>최근 측정값</span><span>{values.length}개</span></div><div className="metric-rows">{values.slice(0, 8).map((value) => <div key={value.value_id}><span><strong>{value.metric_name ?? value.metric_key}</strong><small>{datasetLabels.get(value.dataset_key) ?? value.dataset_key}</small></span><b>{value.value_number ?? value.value_text ?? "—"} <small>{value.unit ?? ""}</small></b></div>)}{!values.length ? <div className="empty">표시할 측정값이 없습니다.</div> : null}</div></div>
                </> : null}
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
                                <b>{value.metric_name ?? value.metric_key}</b> {value.value_number ?? value.value_text ?? "—"}{value.unit ? ` ${value.unit}` : ""}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : <p className="muted-note">예보 데이터가 없습니다.</p>}
                </div>
            </>
          ) : <div className="empty">지도에서 위치를 선택하세요.</div>}
        </MapDetailOverlay> : null}
        </div>
      </MapTabs>
    </section>
  );
}
