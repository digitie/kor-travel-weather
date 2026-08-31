"use client";

import { Crosshair, List, Map as MapIcon, Search, Thermometer, Wind, X } from "lucide-react";
import maplibregl, { type Map as MapLibreMap, type Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";

import { getForecast, getLatest, Location, WeatherValue } from "@/lib/api";

type WeatherMapProps = {
  locations: Location[];
};

function valueFor(values: WeatherValue[], ...keys: string[]) {
  const found = values.find((value) => keys.includes(value.metric_key));
  if (!found) return "—";
  return `${found.value_number ?? found.value_text ?? "—"}${found.unit ? ` ${found.unit}` : ""}`;
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

  const visibleLocations = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return locations;
    return locations.filter((location) =>
      `${location.name} ${location.location_id} ${location.region_code ?? ""}`.toLocaleLowerCase().includes(normalized),
    );
  }, [locations, query]);
  const selected = visibleLocations.find((location) => location.location_id === selectedId) ?? visibleLocations[0];

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
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    setMessage("");
    Promise.all([getLatest(selected.location_id), getForecast(selected.location_id, undefined, undefined, 100)])
      .then(([latest, next]) => {
        if (cancelled) return;
        setValues(latest.data);
        setForecast(next.data);
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
  }, [selected]);

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
          <button aria-controls="weather-map-panel" aria-selected={mode === "map"} className={mode === "map" ? "active" : ""} id="weather-map-tab" onClick={() => setMode("map")} role="tab" type="button"><MapIcon aria-hidden="true" size={15} /> 지도</button>
          <button aria-controls="weather-list-panel" aria-selected={mode === "list"} className={mode === "list" ? "active" : ""} id="weather-list-tab" onClick={() => setMode("list")} role="tab" type="button"><List aria-hidden="true" size={15} /> 목록</button>
        </div>
        <span className="toolbar-count">{visibleLocations.length} locations</span>
      </div>

      <div className={`weather-layout ${mode === "list" ? "list-mode" : ""}`}>
        <div
          aria-labelledby={mode === "map" ? "weather-map-tab" : "weather-list-tab"}
          className="map-card"
          id={mode === "map" ? "weather-map-panel" : "weather-list-panel"}
          role="tabpanel"
          tabIndex={0}
        >
          <div
            ref={mapNode}
            aria-hidden={mode === "list"}
            aria-label="날씨 위치 지도"
            className="map-canvas"
            role="region"
          />
          <div className="map-legend"><span className="legend-dot" /> 활성 날씨 위치 <span className="legend-muted">{visibleLocations.length}곳</span></div>
          {mode === "list" ? (
            <div className="location-list map-list-overlay">
              {visibleLocations.map((location) => (
                <button key={location.location_id} type="button" className={selectedId === location.location_id ? "selected" : ""} onClick={() => { setSelectedId(location.location_id); setMode("map"); }}>
                  <span className="list-pin" /><span><strong>{location.name}</strong><small>{location.location_id} · {location.latitude.toFixed(3)}, {location.longitude.toFixed(3)}</small></span><span className="list-arrow">›</span>
                </button>
              ))}
              {!visibleLocations.length ? <div className="empty">검색 결과가 없습니다.</div> : null}
            </div>
          ) : null}
        </div>
        <aside className="weather-inspector" aria-live="polite">
          {selected ? (
            <>
              <div className="inspector-heading"><div><span className="eyebrow">selected location</span><h2>{selected.name}</h2><p>{selected.location_id} · {selected.latitude.toFixed(4)}, {selected.longitude.toFixed(4)}</p></div><button type="button" className="icon-button" title="지도에서 위치로 이동" onClick={() => map.current?.easeTo({ center: [selected.longitude, selected.latitude], zoom: 10, duration: 500 })}><Crosshair size={17} /></button></div>
              {loading ? <div className="loading-block">최신 날씨를 불러오는 중…</div> : message ? <div className="empty">{message}</div> : <>
                <div className="metric-grid"><div className="metric-card primary"><Thermometer size={17} /><span>기온</span><strong>{valueFor(values, "TMP", "temperature", "temp_c")}</strong></div><div className="metric-card"><Wind size={17} /><span>풍속</span><strong>{valueFor(values, "WSD", "wind_speed", "wind_kph")}</strong></div><div className="metric-card"><span>습도</span><strong>{valueFor(values, "REH", "humidity")}</strong></div><div className="metric-card"><span>강수</span><strong>{valueFor(values, "PCP", "precipitation")}</strong></div></div>
                <div className="inspector-section"><div className="section-label"><span>latest metrics</span><span>{values.length}개</span></div><div className="metric-rows">{values.slice(0, 8).map((value) => <div key={value.value_id}><span><strong>{value.metric_name ?? value.metric_key}</strong><small>{value.dataset_key}</small></span><b>{value.value_number ?? value.value_text ?? "—"} <small>{value.unit ?? ""}</small></b></div>)}{!values.length ? <div className="empty">표시할 metric이 없습니다.</div> : null}</div></div>
                <div className="inspector-section forecast-section"><div className="section-label"><span>forecast preview</span><span>{forecast.length}개</span></div><p className="muted-note">{forecast.length ? `${new Date(forecast[0].target_at).toLocaleString("ko-KR")} 기준 예보` : "예보 데이터가 없습니다."}</p></div>
              </>}
            </>
          ) : <div className="empty">지도에서 위치를 선택하세요.</div>}
        </aside>
      </div>
    </section>
  );
}
