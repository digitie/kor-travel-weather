"use client";

import { Crosshair, List, Map as MapIcon, RefreshCw, Search, Thermometer, Wind, X } from "lucide-react";
import maplibregl, { type Map as MapLibreMap, type Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import { getForecast, getLatest, Location, WeatherValue } from "@/lib/api";

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
      const element = document.createElement("button");
      element.type = "button";
      element.className = `weather-marker${location.location_id === selectedId ? " selected" : ""}`;
      element.title = location.name;
      element.setAttribute("aria-label", `${location.name} 날씨 보기`);
      element.setAttribute("aria-pressed", String(location.location_id === selectedId));
      element.addEventListener("click", () => setSelectedId(location.location_id));
      return new maplibregl.Marker({ element })
        .setLngLat([location.longitude, location.latitude])
        .addTo(map.current!);
    });
    if (selected) {
      map.current.easeTo({ center: [selected.longitude, selected.latitude], duration: 500 });
    }
  }, [selected, selectedId, visibleLocations]);

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
              </>}
            </>
          ) : <div className="empty">지도에서 위치를 선택하세요.</div>}
        </aside>
      </div>
    </section>
  );
}
