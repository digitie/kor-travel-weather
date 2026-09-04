"use client";

import { Crosshair, List, Map as MapIcon, RefreshCw, Search, Thermometer, Wind, X } from "lucide-react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import {
  getForecast,
  getLatest,
  getMarkerSummaries,
  Location,
  WeatherMarker,
  WeatherValue,
} from "@/lib/api";
import {
  VWorldMapView,
  VWorldWeatherMarker,
  type WeatherCondition,
} from "@/components/vworld-map-view";

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

  if (source === "visual_crossing" && !code) {
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

function markerState(summary: WeatherMarker | undefined): MarkerState {
  if (!summary) return { kind: "unknown", glyph: "·", label: "날씨 정보 없음" };
  if (summary.alerts.length) {
    return { kind: "alert", glyph: "!", label: `특보 ${summary.alerts.length}건` };
  }
  const rows = summary.latest;
  const codeRows = rows.filter((row) =>
    ["WEATHER_CODE", "weather_code", "SKY", "PTY"].includes(row.metric_key),
  );
  for (const codeRow of codeRows) {
    if (codeRow.metric_key === "PTY") {
      const code = numericCode(codeRow);
      if (code !== null && code > 0) {
        const snow = code === 3 || code === 7;
        return { kind: snow ? "snow" : "rain", glyph: snow ? "❄" : "☂", label: snow ? "눈" : "강수" };
      }
    }
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
  const row = summary.latest.find((value) =>
    ["TMP", "TEMP", "temperature", "temperature_2m", "temp_c"].includes(value.metric_key),
  );
  if (!row) return null;
  const numeric = row.value_number ?? Number(row.value_text);
  return Number.isFinite(numeric) ? numeric : null;
}

export function WeatherMap({ locations }: WeatherMapProps) {
  const map = useRef<MapLibreMap | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [selectedId, setSelectedId] = useState(locations[0]?.location_id ?? "");
  const [values, setValues] = useState<WeatherValue[]>([]);
  const [forecast, setForecast] = useState<WeatherValue[]>([]);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"map" | "list">("map");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null);
  const [summaries, setSummaries] = useState<Record<string, WeatherMarker>>({});
  const summariesRef = useRef<Record<string, WeatherMarker>>({});
  const markerRefreshRef = useRef(-1);
  const [viewport, setViewport] = useState<MapViewport | null>(null);

  const visibleLocations = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return locations;
    return locations.filter((location) =>
      `${location.name} ${location.location_id} ${location.region_code ?? ""}`.toLocaleLowerCase().includes(normalized),
    );
  }, [locations, query]);
  const selected = visibleLocations.find((location) => location.location_id === selectedId) ?? visibleLocations[0];
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
  const groupedForecast = useMemo(() => forecastGroups(forecast), [forecast]);
  const latestCollectedAt = useMemo(() => {
    const timestamps = values
      .map((value) => Date.parse(value.collected_at))
      .filter((timestamp) => Number.isFinite(timestamp));
    return timestamps.length ? new Date(Math.max(...timestamps)) : null;
  }, [values]);
  const freshnessTimestamp = latestCollectedAt ?? lastRefreshedAt;
  const isFresh = latestCollectedAt !== null && Date.now() - latestCollectedAt.getTime() <= 2 * 60 * 60 * 1000;

  function handleTabKey(event: ReactKeyboardEvent<HTMLButtonElement>, current: "map" | "list") {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" || (event.key === "ArrowLeft" && current === "list") || (event.key === "ArrowRight" && current === "map") ? "map" : "list";
    setMode(next);
    requestAnimationFrame(() => document.getElementById(`weather-${next}-tab`)?.focus());
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
    if (!selected) {
      setValues([]);
      setForecast([]);
      setMessage("");
      return;
    }
    let cancelled = false;
    // Clear the previous location immediately. A failed request must never
    // leave another location's metrics visible in the inspector.
    setValues([]);
    setForecast([]);
    setLoading(true);
    setMessage("");
    Promise.all([getLatest(selected.location_id), getForecast(selected.location_id, undefined, undefined, 100)])
      .then(([latest, next]) => {
        if (cancelled) return;
        setValues(latest.data);
        setForecast(next.data);
        setLastRefreshedAt(new Date());
        if (!latest.data.length) setMessage("아직 수집된 최신 fact가 없습니다.");
      })
      .catch((reason: unknown) => {
        if (!cancelled) setMessage(reason instanceof Error ? reason.message : "날씨를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshToken, selected]);

  useEffect(() => {
    if (!mapLocations.length || mode !== "map") return;
    let cancelled = false;
    if (markerRefreshRef.current !== refreshToken) {
      markerRefreshRef.current = refreshToken;
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
    const loadChunks = async () => {
      try {
        for (let index = 0; index < chunks.length; index += MARKER_BATCH_CONCURRENCY) {
          const responses = await Promise.allSettled(
            chunks
              .slice(index, index + MARKER_BATCH_CONCURRENCY)
              .map((chunk) => getMarkerSummaries(chunk)),
          );
          if (cancelled) return;
          for (const response of responses) {
            if (response.status !== "fulfilled") continue;
            for (const item of response.value.data) summariesRef.current[item.location_id] = item;
          }
          setSummaries({ ...summariesRef.current });
        }
      } catch {
        if (!cancelled) setSummaries({ ...summariesRef.current });
      }
    };
    void loadChunks();
    return () => {
      cancelled = true;
    };
  }, [mapLocations, mode, refreshToken]);

  useEffect(() => {
    if (mode !== "map" || !map.current) return;
    requestAnimationFrame(() => map.current?.resize());
  }, [mode]);

  return (
    <section className="weather-workbench" aria-label="지도 기반 날씨 조회">
      <div className="workbench-toolbar">
        <div className="search-box">
          <Search size={16} aria-hidden="true" />
          <input
            aria-label="위치 검색"
            placeholder="도시·위치 검색"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {query ? <button type="button" aria-label="검색어 지우기" onClick={() => setQuery("")}><X size={15} /></button> : null}
        </div>
        <div className="view-switcher" role="tablist" aria-label="조회 방식">
          <button aria-controls="weather-map-panel" aria-selected={mode === "map"} className={mode === "map" ? "active" : ""} id="weather-map-tab" onClick={() => setMode("map")} onKeyDown={(event) => handleTabKey(event, "map")} role="tab" tabIndex={mode === "map" ? 0 : -1} type="button"><MapIcon aria-hidden="true" size={15} /> 지도</button>
          <button aria-controls="weather-list-panel" aria-selected={mode === "list"} className={mode === "list" ? "active" : ""} id="weather-list-tab" onClick={() => setMode("list")} onKeyDown={(event) => handleTabKey(event, "list")} role="tab" tabIndex={mode === "list" ? 0 : -1} type="button"><List aria-hidden="true" size={15} /> 목록</button>
        </div>
        <span className="toolbar-count">{visibleLocations.length} locations</span>
      </div>

      <div className={`weather-layout ${mode === "list" ? "list-mode" : ""}`}>
        <div className="map-card">
          <div aria-labelledby="weather-map-tab" className="map-panel" hidden={mode !== "map"} id="weather-map-panel" role="tabpanel" tabIndex={0}>
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
              {mapLocations.map((location) => {
                const summary = summaries[location.location_id];
                const state = markerState(summary);
                return (
                  <VWorldWeatherMarker
                    alertCount={summary?.alerts.length ?? 0}
                    ariaLabel={`${location.name}: ${state.label} 날씨 보기`}
                    condition={markerCondition(state.kind)}
                    key={location.location_id}
                    lngLat={[location.longitude, location.latitude]}
                    onClick={() => setSelectedId(location.location_id)}
                    selected={location.location_id === selectedId}
                    temperature={markerTemperature(summary)}
                    title={location.name}
                  />
                );
              })}
            </VWorldMapView>
            <div className="map-legend"><span className="legend-dot" /> 지도 표시 위치 <span className="legend-muted">{mapLocations.length}/{visibleLocations.length}곳</span></div>
          </div>
          <div aria-labelledby="weather-list-tab" className="list-panel" hidden={mode !== "list"} id="weather-list-panel" role="tabpanel" tabIndex={0}>
            <div className="location-list map-list-overlay">
              {visibleLocations.map((location) => (
                <button aria-pressed={selectedId === location.location_id} key={location.location_id} type="button" className={selectedId === location.location_id ? "selected" : ""} onClick={() => { setSelectedId(location.location_id); setMode("map"); }}>
                  <span className="list-pin" /><span><strong>{location.name}</strong><small>{location.location_id} · {location.latitude.toFixed(3)}, {location.longitude.toFixed(3)}</small></span><span className="list-arrow">›</span>
                </button>
              ))}
              {!visibleLocations.length ? <div className="empty">검색 결과가 없습니다.</div> : null}
            </div>
          </div>
        </div>
        <aside className="weather-inspector" aria-live="polite">
          {selected ? (
            <>
              <div className="inspector-heading"><div><span className="eyebrow">selected location</span><h2>{selected.name}</h2><p>{selected.location_id} · {selected.latitude.toFixed(4)}, {selected.longitude.toFixed(4)}</p><div className="inspector-freshness"><span className={`status ${isFresh ? "on" : "warn"}`}>{isFresh ? "fresh" : "stale"}</span><small>{freshnessTimestamp ? freshnessTimestamp.toLocaleString("ko-KR") : "수집 시각 없음"}</small>{lastRefreshedAt ? <small>확인 {lastRefreshedAt.toLocaleTimeString("ko-KR")}</small> : null}<small>{values[0]?.provider ?? "provider 없음"} · {values[0]?.dataset_key ?? "dataset 없음"}</small>{values[0]?.source_record_key ? <small>source {values[0].source_record_key.slice(0, 12)}…</small> : null}</div></div><div className="inspector-actions"><button type="button" className="icon-button" aria-label="날씨 새로고침" title="날씨 새로고침" onClick={() => setRefreshToken((value) => value + 1)}><RefreshCw size={17} /></button><button type="button" className="icon-button" aria-label="지도에서 위치로 이동" title="지도에서 위치로 이동" onClick={() => map.current?.easeTo({ center: [selected.longitude, selected.latitude], zoom: 10, duration: 500 })}><Crosshair size={17} /></button></div></div>
              {loading ? <div className="loading-block" role="status" aria-live="polite">최신 날씨를 불러오는 중…</div> : <>
                {message ? <div className="empty" role="status">{message}</div> : null}
                {summaries[selected.location_id]?.measurement_point ? (
                  <div className="measurement-point">
                    <span className="section-label">측정 지점</span>
                    <strong>{summaries[selected.location_id].measurement_point?.station_name}</strong>
                    <small>
                      {summaries[selected.location_id].measurement_point?.address ?? ""} · {summaries[selected.location_id].measurement_point?.distance_km.toFixed(1)}km
                    </small>
                  </div>
                ) : null}
                <div className="metric-grid"><div className="metric-card primary"><Thermometer size={17} /><span>기온</span><strong>{valueFor(values, "TMP", "TEMP", "temperature", "temperature_2m", "temp_c")}</strong></div><div className="metric-card"><Wind size={17} /><span>풍속</span><strong>{valueFor(values, "WSD", "WIND_SPEED", "wind_speed", "wind_speed_10m", "wind_kph")}</strong></div><div className="metric-card"><span>습도</span><strong>{valueFor(values, "REH", "HUMIDITY", "relative_humidity_2m", "humidity")}</strong></div><div className="metric-card"><span>강수</span><strong>{valueFor(values, "PCP", "PRECIP", "precipitation", "precipitation_sum", "precip_mm")}</strong></div></div>
                <div className="inspector-section"><div className="section-label"><span>latest metrics</span><span>{values.length}개</span></div><div className="metric-rows">{values.slice(0, 8).map((value) => <div key={value.value_id}><span><strong>{value.metric_name ?? value.metric_key}</strong><small>{value.dataset_key}</small></span><b>{value.value_number ?? value.value_text ?? "—"} <small>{value.unit ?? ""}</small></b></div>)}{!values.length ? <div className="empty">표시할 metric이 없습니다.</div> : null}</div></div>
                <div className="inspector-section forecast-section">
                  <div className="section-label"><span>forecast preview</span><span>{forecast.length}개</span></div>
                  {groupedForecast.length ? (
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
                {summaries[selected.location_id]?.alerts.length ? (
                  <div className="alert-panel" role="status">
                    <strong>기상특보</strong>
                    {summaries[selected.location_id].alerts.slice(0, 3).map((alert) => (
                      <span key={alert.value_id}>
                        {alert.value_text ?? alert.metric_name ?? "특보"} · {alert.severity ?? "advisory"}
                      </span>
                    ))}
                  </div>
                ) : null}
              </>}
            </>
          ) : <div className="empty">지도에서 위치를 선택하세요.</div>}
        </aside>
      </div>
    </section>
  );
}
