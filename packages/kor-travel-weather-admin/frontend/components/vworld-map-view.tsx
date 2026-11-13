"use client";

// maplibre-gl v6 is ESM-only and dropped its default export; a namespace
// import keeps every `maplibregl.X` reference below working as both a value
// (`new maplibregl.Map(...)`, `new maplibregl.Marker(...)`) and a type
// (`maplibregl.ErrorEvent`, `maplibregl.IControl`), which named imports alone
// could not do without renaming every call site.
import * as maplibregl from "maplibre-gl";
import type {
  Map as MapLibreMap,
  MapLibreEvent,
  Marker as MapLibreMarker,
  PointLike,
  PositionAnchor,
} from "maplibre-gl";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import type { CSSProperties, ReactNode } from "react";
import { abbreviateWeatherRegionName } from "@/lib/weather-regions";

import {
  isVWorldApiKeyConfigured,
  redactVWorldUrl,
  type VWorldLayerType,
} from "@/lib/vworld-style";
import { VWorldMapView as UpstreamMapView, Marker as UpstreamMarker, useMap } from "vworld-map-web";

import {
  buildWeatherClusterData,
  weatherConditionIcon,
  weatherConditionLabel,
  type WeatherClusterData,
  type WeatherClusterMarker,
  type WeatherCondition,
} from "@/lib/weather-clusters";

import "maplibre-gl/dist/maplibre-gl.css";

// Next.js's asset handling hashes the worker without its `maplibre-gl-shared.mjs`
// sibling, so the auto-detected worker URL 404s and every map mounts with no
// tiles. `scripts/copy-maplibre-worker.mjs` (predev/prebuild) copies both files
// to public/maplibre; this points the client at them. Must run before the first
// `new maplibregl.Map(...)`, which is why it sits at module scope in the one file
// that constructs one, rather than inside the component.
maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

// 지도 생성·제거·카메라·스타일·마커 생명주기는 GitHub 고정 산출물이 소유한다.
// 이 어댑터는 Weather의 무키 중립 배경과 타일 오류 회복 표시만 연결한다.
export type VWorldMapViewProps = {
  apiKey?: string;
  center: [number, number];
  zoom: number;
  layerType?: VWorldLayerType;
  minZoom?: number;
  maxZoom?: number;
  navigation?: boolean;
  scale?: boolean;
  className?: string;
  style?: CSSProperties;
  /** 지도 래퍼의 data-testid. kor-travel-map의 VWorldMapView testId와 같은 계약이다. */
  testId?: string;
  children?: ReactNode;
  onLoad?: (map: MapLibreMap) => void;
  onMoveEnd?: (map: MapLibreMap, event: MapLibreEvent) => void;
  onError?: (event: maplibregl.ErrorEvent) => void;
  onRecover?: () => void;
};

export const useVWorldMap = useMap;

// API 키가 없을 때도 위치 마커·이동·확대는 제공한다. upstream의 hasApiKey 경계에는
// 공개 sentinel을 넘기고 모든 raster 요청은 로컬 data URI로 바꿔 외부 요청을 막는다.
const NEUTRAL_TILE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGN4+e4LAAWPAsxuc85WAAAAAElFTkSuQmCC";

type TileRecoveryState = {
  failedTiles: Map<string, maplibregl.ErrorEvent>;
  shown: boolean;
  blocked: boolean;
};

function rememberTileError(state: TileRecoveryState, event: maplibregl.ErrorEvent) {
  const key = rasterTileKey(event as unknown as maplibregl.MapSourceDataEvent);
  if (key) state.failedTiles.set(key, event);
  else state.blocked = true;
  state.shown = true;
}

function TileRecovery({ state, onError, onRecover }: Pick<VWorldMapViewProps, "onError" | "onRecover"> & { state: TileRecoveryState }) {
  const map = useMap();
  const callbacks = useRef({ onError, onRecover });
  useLayoutEffect(() => { callbacks.current = { onError, onRecover }; }, [onError, onRecover]);
  useEffect(() => {
    if (!map) return;
    // upstream은 load 뒤 children을 연다. 최초 load 전 실패도 부모의 onError에 보존한다.
    const handleSourceData = (event: maplibregl.MapSourceDataEvent) => {
      const key = rasterTileKey(event);
      if (key && event.tile?.state === "loaded") state.failedTiles.delete(key);
    };
    const handleIdle = () => {
      if (state.blocked || !map.isStyleLoaded() || !map.areTilesLoaded()) return;
      const visibleTiles = new Set(Object.entries(map.getStyle().sources).flatMap(([sourceId, source]) => source.type === "raster"
        ? map.coveringTiles({ tileSize: source.tileSize ?? 512, minzoom: source.minzoom, maxzoom: source.maxzoom, roundZoom: true }).map(tile => `${sourceId}/${tile.key}`) : []));
      const visibleError = Array.from(state.failedTiles).find(([key]) => visibleTiles.has(key))?.[1];
      if (visibleError && !state.shown) {
        state.shown = true;
        callbacks.current.onError?.(visibleError);
      } else if (!visibleError && state.shown && visibleTiles.size) {
        state.shown = false;
        callbacks.current.onRecover?.();
      }
    };
    map.on("sourcedata", handleSourceData);
    map.on("idle", handleIdle);
    return () => {
      map.off("sourcedata", handleSourceData);
      map.off("idle", handleIdle);
    };
  }, [map, state]);
  return null;
}

function rasterTileKey(event: { sourceId?: string; tile?: { tileID?: { key?: string } } }): string | null {
  return event.sourceId && event.tile?.tileID?.key ? `${event.sourceId}/${event.tile.tileID.key}` : null;
}

export function VWorldMapView({ apiKey, center, zoom, layerType = "Base", minZoom = 6,
  maxZoom = 19, navigation = true, scale = false, className, style, testId, children,
  onLoad, onMoveEnd, onError, onRecover,
}: VWorldMapViewProps) {
  const configured = isVWorldApiKeyConfigured(apiKey);
  const recovery = useMemo<TileRecoveryState>(() => ({ failedTiles: new Map(), shown: false, blocked: false }), []);
  return <div aria-label="VWorld 날씨 지도" role="application" className={className} style={style} data-testid={testId}
    data-vworld-basemap={configured ? layerType : "fallback"} data-vworld-key={configured ? "configured" : "missing"}>
    <UpstreamMapView apiKey={configured ? apiKey.trim() : "weather-neutral-basemap"}
      center={center} zoom={zoom} layerType={layerType} minZoom={minZoom} maxZoom={maxZoom}
      navigation={navigation} scale={scale} geolocate={false}
      transformRequest={configured ? undefined : () => ({ url: NEUTRAL_TILE })}
      onLoad={onLoad} onMoveEnd={event => onMoveEnd?.(event.target, event)}
      onError={event => {
        // 최초 load 이전의 타일 오류도 사용자에게 전달한다.
        rememberTileError(recovery, event);
        onError?.(event);
        if (!onError) console.warn("[VWorldMapView]", redactVWorldUrl(event.error?.message));
      }}
      fallback={<p role="alert">지도를 초기화하지 못했습니다. 위치 목록을 이용해 주세요.</p>}>
      <TileRecovery state={recovery} onError={onError} onRecover={onRecover} />
      {children}
    </UpstreamMapView>
  </div>;
}

export type VWorldMarkerProps = {
  lngLat: [number, number];
  anchor?: PositionAnchor;
  offset?: PointLike;
  className?: string;
  selected?: boolean;
  ariaLabel?: string;
  title?: string;
  onClick?: (event: MouseEvent, marker: MapLibreMarker) => void;
  children: ReactNode;
};

/** Weather 내용은 앱이, 좌표·포털·마커 생명주기는 upstream이 소유한다. */
export function VWorldMarker({ onClick, title, ariaLabel, children, ...props }: VWorldMarkerProps) {
  return <UpstreamMarker {...props} onClick={onClick ? (event, _context, marker) => onClick(event, marker) : undefined}>
    <span title={title} aria-label={ariaLabel}>{children}</span>
  </UpstreamMarker>;
}

export type VWorldWeatherMarkerProps = Omit<VWorldMarkerProps, "children"> & {
  temperature: number | null;
  regionLabel?: string;
  condition: WeatherCondition;
  alertCount?: number;
  regionKey?: string;
};

export function WeatherMarkerLabel({ temperature, condition, alertCount = 0, regionLabel }: Pick<VWorldWeatherMarkerProps, "temperature" | "condition" | "alertCount" | "regionLabel">) {
  return <>
    <span aria-hidden="true" className="vworld-weather-marker-icon">{weatherConditionIcon(condition)}</span>
    <span className="vworld-weather-marker-copy">{regionLabel ? <small title={regionLabel}>{abbreviateWeatherRegionName(regionLabel)}</small> : null}
      <strong>{temperature === null ? "—" : `${Math.round(temperature)}°`}</strong>
      <small>{weatherConditionLabel(condition)}</small>
    </span>
    {alertCount > 0 ? <span aria-label={`특보 ${alertCount}건`} className="weather-marker-badge">{alertCount}</span> : null}
  </>;
}

/**
 * Weather marker from maplibre-vworld-react's WeatherMarker model, adapted to
 * the admin's data contract. It uses a deterministic pill/pin visual and
 * keeps the alert count visible without putting provider keys in the DOM.
 */
export function VWorldWeatherMarker({
  temperature,
  condition,
  alertCount = 0,
  regionLabel,
  regionKey,
  ...markerProps
}: VWorldWeatherMarkerProps) {
  return (
    <VWorldMarker
      {...markerProps}
      // The expanded weather marker is a pill, not a pointed pin. Its
      // geographic coordinate must therefore be its visual centre. Callers
      // can still opt into `anchor="bottom"` when providing a pointed
      // marker shape.
      anchor={markerProps.anchor ?? "center"}
      className={`weather-marker vworld-weather-marker weather-marker-${condition}${regionKey ? " weather-region-marker" : ""}${markerProps.selected ? " selected" : ""}`}
    >
      {regionKey ? <><svg className="weather-region-link" aria-hidden="true" width="1" height="1"><line x1="0" y1="0" x2="0" y2="0" /></svg><span className="weather-region-origin" aria-hidden="true" /></> : null}
      <button
        aria-label={markerProps.ariaLabel}
        aria-pressed={markerProps.selected}
        className="vworld-weather-marker-button"
        title={markerProps.title}
        type="button"
      >
        <WeatherMarkerLabel temperature={temperature} condition={condition} alertCount={alertCount} regionLabel={regionLabel} />
      </button>
    </VWorldMarker>
  );
}

const WEATHER_CLUSTER_SOURCE_ID = "kor-weather-marker-clusters";

function createWeatherClusterElement(
  pointCount: number,
  label: string,
  onClick: () => void,
): HTMLButtonElement {
  const element = document.createElement("button");
  element.type = "button";
  element.className = "vworld-weather-cluster";
  element.dataset.clusterSize = pointCount < 100 ? "small" : pointCount < 1000 ? "medium" : "large";
  element.textContent = label;
  element.title = `${pointCount}개 위치 확대`;
  element.setAttribute("aria-label", `날씨 위치 ${pointCount}개 묶음. 클릭하면 확대합니다.`);
  element.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick();
  });
  return element;
}

function createWeatherClusterPointElement(
  marker: WeatherClusterMarker,
  onClick: () => void,
): HTMLDivElement {
  // Keep the MapLibre root as the transparent, centre-anchored wrapper used
  // by VWorldWeatherMarker. The styled control must be a child; applying both
  // wrapper and button classes to one element would let the wrapper's reset
  // styles hide the weather chip.
  const element = document.createElement("div");
  element.className = `weather-marker vworld-weather-marker weather-marker-${marker.condition}${marker.selected ? " selected" : ""}`;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "vworld-weather-marker-button";
  button.setAttribute("aria-label", marker.ariaLabel);
  button.setAttribute("aria-pressed", String(marker.selected === true));
  if (marker.title) button.title = marker.title;
  const icon = document.createElement("span");
  icon.className = "vworld-weather-marker-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = weatherConditionIcon(marker.condition);
  const copy = document.createElement("span");
  copy.className = "vworld-weather-marker-copy";
  const temperature = document.createElement("strong");
  temperature.textContent = marker.temperature === null ? "—" : `${Math.round(marker.temperature)}°`;
  const label = document.createElement("small");
  label.textContent = weatherConditionLabel(marker.condition);
  copy.append(temperature, label);
  button.append(icon, copy);
  if ((marker.alertCount ?? 0) > 0) {
    const badge = document.createElement("span");
    badge.className = "weather-marker-badge";
    badge.textContent = String(marker.alertCount);
    badge.setAttribute("aria-label", `특보 ${marker.alertCount}건`);
    button.append(badge);
  }
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick();
  });
  element.append(button);
  return element;
}

function weatherClusterMarkerKey(marker: WeatherClusterMarker): string {
  return [
    marker.id,
    marker.lngLat[0],
    marker.lngLat[1],
    marker.temperature,
    marker.condition,
    marker.alertCount ?? 0,
    marker.selected === true,
    marker.ariaLabel,
    marker.title ?? "",
  ].join("|");
}

/**
 * Weather-specific native MapLibre clustering. The source uses MapLibre's
 * worker-backed GeoJSON clustering while the visible cluster and weather point
 * markers remain DOM buttons, preserving the library marker look and keyboard
 * accessibility. Only features currently rendered in the viewport receive a
 * DOM marker, so a nationwide station catalog does not create hundreds of
 * React portals on every pan.
 */
export function VWorldWeatherClusters({
  markers,
  clusterRadius = 60,
  clusterMaxZoom = 14,
}: {
  markers: ReadonlyArray<WeatherClusterMarker>;
  clusterRadius?: number;
  clusterMaxZoom?: number;
}) {
  const map = useVWorldMap();
  const data = useMemo(() => buildWeatherClusterData(markers), [markers]);
  const dataRef = useRef<WeatherClusterData>(data);
  useLayoutEffect(() => { dataRef.current = data; }, [data]);
  const markerByIdRef = useRef(new Map<string, WeatherClusterMarker>());
  useLayoutEffect(() => { markerByIdRef.current = new Map(markers.map((marker) => [marker.id, marker])); }, [markers]);

  useEffect(() => {
    if (!map) return;
    const source = map.getSource(WEATHER_CLUSTER_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
    source?.setData(data);
  }, [data, map]);

  useEffect(() => {
    if (!map) return;
    const sourceId = WEATHER_CLUSTER_SOURCE_ID;
    const clusterLayerId = `${sourceId}-clusters`;
    const pointLayerId = `${sourceId}-points`;
    const markerPool = new Map<string, maplibregl.Marker>();
    const markerKeys = new Map<string, string>();
    let onScreen = new Set<string>();
    let frame = 0;

    const ensureSource = () => {
      if (!map.isStyleLoaded()) return false;
      if (!map.getSource(sourceId)) {
        map.addSource(sourceId, {
          type: "geojson",
          data: dataRef.current,
          cluster: true,
          clusterRadius,
          clusterMaxZoom,
        });
      }
      if (!map.getLayer(clusterLayerId)) {
        map.addLayer({
          id: clusterLayerId,
          type: "circle",
          source: sourceId,
          filter: ["has", "point_count"],
          paint: { "circle-radius": 1, "circle-opacity": 0 },
        });
      }
      if (!map.getLayer(pointLayerId)) {
        map.addLayer({
          id: pointLayerId,
          type: "circle",
          source: sourceId,
          filter: ["!", ["has", "point_count"]],
          paint: { "circle-radius": 1, "circle-opacity": 0 },
        });
      }
      return true;
    };

    const removeMarker = (id: string) => {
      markerPool.get(id)?.remove();
      markerPool.delete(id);
      markerKeys.delete(id);
    };

    const updateMarkers = () => {
      frame = 0;
      if (!ensureSource()) return;
      const next = new Set<string>();
      const seen = new Set<string>();
      for (const feature of map.querySourceFeatures(sourceId)) {
        if (feature.geometry.type !== "Point") continue;
        const coordinates = feature.geometry.coordinates as [number, number];
        const properties = (feature.properties ?? {}) as Record<string, unknown>;
        const isCluster = properties.point_count !== undefined;
        if (isCluster) {
          const clusterId = Number(properties.cluster_id);
          if (!Number.isFinite(clusterId)) continue;
          const id = `cluster-${clusterId}`;
          if (seen.has(id)) continue;
          seen.add(id);
          const count = Number(properties.point_count) || 0;
          const label = String(properties.point_count_abbreviated ?? count);
          const clusterKey = `${count}|${label}|${coordinates[0]}|${coordinates[1]}`;
          if (markerKeys.get(id) !== clusterKey) {
            const wasOnScreen = onScreen.has(id);
            removeMarker(id);
            const element = createWeatherClusterElement(count, label, () => {
              const clusterSource = map.getSource(sourceId) as maplibregl.GeoJSONSource | undefined;
              if (!clusterSource) return;
              void clusterSource.getClusterExpansionZoom(clusterId).then((zoom) => {
                map.easeTo({ center: coordinates, zoom, duration: 350 });
              }).catch(() => {
                // The cluster can disappear between the click and expansion.
              });
            });
            const nextMarker = new maplibregl.Marker({ element }).setLngLat(coordinates);
            markerPool.set(id, nextMarker);
            markerKeys.set(id, clusterKey);
            if (wasOnScreen) nextMarker.addTo(map);
          } else {
            markerPool.get(id)?.setLngLat(coordinates);
          }
          next.add(id);
          if (!onScreen.has(id)) markerPool.get(id)?.addTo(map);
          continue;
        }

        const markerId = String(properties.marker_id ?? "");
        const marker = markerByIdRef.current.get(markerId);
        if (!marker || seen.has(`point-${markerId}`)) continue;
        const id = `point-${markerId}`;
        seen.add(id);
        const key = weatherClusterMarkerKey(marker);
        if (markerKeys.get(id) !== key) {
          const wasOnScreen = onScreen.has(id);
          removeMarker(id);
          const element = createWeatherClusterPointElement(marker, () => {
            markerByIdRef.current.get(markerId)?.onClick?.();
          });
          const nextMarker = new maplibregl.Marker({ element }).setLngLat(coordinates);
          markerPool.set(id, nextMarker);
          markerKeys.set(id, key);
          if (wasOnScreen) nextMarker.addTo(map);
        } else {
          markerPool.get(id)?.setLngLat(coordinates);
        }
        next.add(id);
        if (!onScreen.has(id)) markerPool.get(id)?.addTo(map);
      }
      for (const id of onScreen) {
        if (!next.has(id)) removeMarker(id);
      }
      onScreen = next;
    };

    const scheduleUpdate = () => {
      if (frame !== 0) return;
      frame = requestAnimationFrame(updateMarkers);
    };
    const handleStyleData = () => {
      if (ensureSource()) scheduleUpdate();
    };

    ensureSource();
    map.on("moveend", scheduleUpdate);
    map.on("zoomend", scheduleUpdate);
    map.on("sourcedata", scheduleUpdate);
    map.on("idle", scheduleUpdate);
    map.on("styledata", handleStyleData);
    scheduleUpdate();

    return () => {
      if (frame !== 0) cancelAnimationFrame(frame);
      map.off("moveend", scheduleUpdate);
      map.off("zoomend", scheduleUpdate);
      map.off("sourcedata", scheduleUpdate);
      map.off("idle", scheduleUpdate);
      map.off("styledata", handleStyleData);
      for (const marker of markerPool.values()) marker.remove();
      markerPool.clear();
      markerKeys.clear();
      onScreen = new Set();
      try {
        if (map.getLayer(clusterLayerId)) map.removeLayer(clusterLayerId);
        if (map.getLayer(pointLayerId)) map.removeLayer(pointLayerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
      } catch {
        // MapLibre may already be tearing down the style.
      }
    };
  }, [clusterMaxZoom, clusterRadius, map]);

  return null;
}
