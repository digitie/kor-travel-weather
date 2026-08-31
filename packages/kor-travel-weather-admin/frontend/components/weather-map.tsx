"use client";

import { Crosshair, List, Map as MapIcon, RefreshCw, Search, Thermometer, Wind, X } from "lucide-react";
import maplibregl, { type Map as MapLibreMap, type Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
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

const SVG_NS = "http://www.w3.org/2000/svg";

function markerIcon(kind: MarkerState["kind"]): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  const add = (tag: string, attributes: Record<string, string>) => {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    svg.append(node);
  };
  if (kind === "clear") {
    add("circle", { cx: "12", cy: "12", r: "4" });
    for (const [x1, y1, x2, y2] of [
      [12, 2, 12, 5], [12, 19, 12, 22], [2, 12, 5, 12], [19, 12, 22, 12],
      [4.9, 4.9, 7, 7], [17, 17, 19.1, 19.1], [17, 7, 19.1, 4.9], [4.9, 19.1, 7, 17],
    ]) add("line", { x1: String(x1), y1: String(y1), x2: String(x2), y2: String(y2) });
  } else if (kind === "cloud") {
    add("path", { d: "M6.5 18h10.7a4.3 4.3 0 0 0 .4-8.6A6 6 0 0 0 6 10.5 3.8 3.8 0 0 0 6.5 18Z" });
  } else if (kind === "rain") {
    add("path", { d: "M5.5 15.5h10a3.5 3.5 0 0 0 .3-7A5 5 0 0 0 7 9a3.2 3.2 0 0 0-1.5 6.5Z" });
    add("line", { x1: "8", y1: "18", x2: "7", y2: "21" });
    add("line", { x1: "13", y1: "18", x2: "12", y2: "21" });
    add("line", { x1: "18", y1: "18", x2: "17", y2: "21" });
  } else if (kind === "snow") {
    add("path", { d: "M6 15.5h10.5a3.5 3.5 0 0 0 .3-7A5 5 0 0 0 7 9a3.2 3.2 0 0 0-1 6.5Z" });
    add("path", { d: "m9 18 3 3m0-3-3 3m6-3 2 2m0-2-2 2" });
  } else if (kind === "storm") {
    add("path", { d: "M6 14.5h10.5a3.5 3.5 0 0 0 .3-7A5 5 0 0 0 7 8a3.2 3.2 0 0 0-1 6.5Z" });
    add("path", { d: "m13 14-3 5h3l-1 4 4-6h-3l2-3Z", fill: "currentColor" });
  } else if (kind === "alert") {
    add("circle", { cx: "12", cy: "12", r: "8.5" });
    add("line", { x1: "12", y1: "7", x2: "12", y2: "13" });
    add("circle", { cx: "12", cy: "17", r: "0.7", fill: "currentColor", stroke: "none" });
  } else {
    add("circle", { cx: "12", cy: "12", r: "2.5", fill: "currentColor", stroke: "none" });
  }
  return svg;
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

function mapStyle() {
  return {
    version: 8 as const,
    sources: {
      osm: {
        type: "raster" as const,
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "© OpenStreetMap contributors",
      },
    },
    layers: [{ id: "osm", type: "raster" as const, source: "osm" }],
  };
}

export function WeatherMap({ locations }: WeatherMapProps) {
  const mapNode = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const markers = useRef<Marker[]>([]);
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

  const visibleLocations = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return locations;
    return locations.filter((location) =>
      `${location.name} ${location.location_id} ${location.region_code ?? ""}`.toLocaleLowerCase().includes(normalized),
    );
  }, [locations, query]);
  const selected = visibleLocations.find((location) => location.location_id === selectedId) ?? visibleLocations[0];
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
    if (!mapNode.current || map.current) return;
    const initial = locations[0];
    const instance = new maplibregl.Map({
      container: mapNode.current,
      style: mapStyle(),
      center: initial ? [initial.longitude, initial.latitude] : [127.8, 36.2],
      zoom: initial ? 7.2 : 6,
      attributionControl: { compact: true },
    });
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.current = instance;
    return () => {
      markers.current.forEach((marker) => marker.remove());
      markers.current = [];
      instance.remove();
      map.current = null;
    };
  }, [locations]);

  useEffect(() => {
    if (!map.current) return;
    markers.current.forEach((marker) => marker.remove());
    markers.current = visibleLocations.map((location) => {
      const state = markerState(summaries[location.location_id]);
      const element = document.createElement("button");
      element.type = "button";
      element.className = `weather-marker weather-marker-${state.kind}${location.location_id === selectedId ? " selected" : ""}`;
      element.title = location.name;
      element.setAttribute("aria-label", `${location.name}: ${state.label} 날씨 보기`);
      element.setAttribute("aria-pressed", String(location.location_id === selectedId));
      const glyph = document.createElement("span");
      glyph.className = "weather-marker-glyph";
      glyph.append(markerIcon(state.kind));
      element.append(glyph);
      if (state.kind === "alert") {
        const alert = document.createElement("span");
        alert.className = "weather-marker-badge";
        alert.setAttribute("aria-hidden", "true");
        alert.textContent = String(summaries[location.location_id]?.alerts.length ?? "!");
        element.append(alert);
      }
      element.addEventListener("click", () => setSelectedId(location.location_id));
      return new maplibregl.Marker({ element })
        .setLngLat([location.longitude, location.latitude])
        .addTo(map.current!);
    });
    if (selected) {
      map.current.easeTo({ center: [selected.longitude, selected.latitude], duration: 500 });
    }
  }, [selected, selectedId, summaries, visibleLocations]);

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
    if (!locations.length) return;
    let cancelled = false;
    const chunks: string[][] = [];
    for (let index = 0; index < locations.length; index += 500) {
      chunks.push(locations.slice(index, index + 500).map((location) => location.location_id));
    }
    Promise.all(chunks.map((chunk) => getMarkerSummaries(chunk)))
      .then((responses) => {
        if (!cancelled) {
          const all = responses.flatMap((response) => response.data);
          setSummaries(Object.fromEntries(all.map((item) => [item.location_id, item])));
        }
      })
      .catch(() => {
        if (!cancelled) setSummaries({});
      });
    return () => {
      cancelled = true;
    };
  }, [locations, refreshToken]);

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
            <div
              ref={mapNode}
              aria-hidden={mode !== "map"}
              aria-label="날씨 위치 지도"
              className="map-canvas"
              role="region"
            />
            <div className="map-legend"><span className="legend-dot" /> 활성 날씨 위치 <span className="legend-muted">{visibleLocations.length}곳</span></div>
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
