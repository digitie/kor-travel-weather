"use client";

import maplibregl, {
  type Map as MapLibreMap,
  type MapLibreEvent,
  type Marker as MapLibreMarker,
  type PointLike,
  type PositionAnchor,
} from "maplibre-gl";
import { createPortal } from "react-dom";
import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

import {
  buildVWorldStyle,
  getVWorldMaxZoom,
  isVWorldApiKeyConfigured,
  redactVWorldUrl,
  type VWorldLayerType,
} from "@/lib/vworld-style";
import {
  buildWeatherClusterData,
  type WeatherClusterData,
  type WeatherClusterMarker,
  type WeatherCondition,
} from "@/lib/weather-clusters";

import "maplibre-gl/dist/maplibre-gl.css";

/**
 * React/MapLibre boundary modelled on digitie's maplibre-vworld-react
 * `vworld-map-web` package.  The app keeps the adapter local because that
 * GitHub monorepo intentionally does not publish an npm package; the public
 * props and VWorld style rules remain compatible with its VWorldMapView.
 */
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
  children?: ReactNode;
  onLoad?: (map: MapLibreMap) => void;
  onMoveEnd?: (map: MapLibreMap, event: MapLibreEvent) => void;
  onError?: (event: maplibregl.ErrorEvent) => void;
};

type MapContextValue = MapLibreMap | null;
const VWorldMapContext = createContext<MapContextValue>(null);

export function useVWorldMap(): MapLibreMap | null {
  return useContext(VWorldMapContext);
}

export function VWorldMapView({
  apiKey,
  center,
  zoom,
  layerType = "Base",
  minZoom = 6,
  maxZoom = 19,
  navigation = true,
  scale = false,
  className,
  style,
  children,
  onLoad,
  onMoveEnd,
  onError,
}: VWorldMapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [map, setMap] = useState<MapLibreMap | null>(null);
  const [loaded, setLoaded] = useState(false);
  const onLoadRef = useRef(onLoad);
  const onMoveEndRef = useRef(onMoveEnd);
  const onErrorRef = useRef(onError);
  useLayoutEffect(() => {
    onLoadRef.current = onLoad;
    onMoveEndRef.current = onMoveEnd;
    onErrorRef.current = onError;
  }, [onLoad, onMoveEnd, onError]);

  const initialOptionsRef = useRef({
    center,
    zoom,
    minZoom,
    maxZoom: Math.min(maxZoom, getVWorldMaxZoom(layerType)),
    layerType,
    apiKey,
  });
  const initialOptions = initialOptionsRef.current;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) return;

    let instance: MapLibreMap;
    try {
      instance = new maplibregl.Map({
        container,
        style: buildVWorldStyle(initialOptions.apiKey, initialOptions.layerType),
        center: initialOptions.center,
        zoom: initialOptions.zoom,
        minZoom: initialOptions.minZoom,
        maxZoom: initialOptions.maxZoom,
        attributionControl: { compact: true },
      });
    } catch (reason) {
      const error = reason instanceof Error ? reason : new Error(String(reason));
      onErrorRef.current?.({ type: "error", error } as maplibregl.ErrorEvent);
      return;
    }

    mapRef.current = instance;
    setMap(instance);
    const handleLoad = () => {
      setLoaded(true);
      onLoadRef.current?.(instance);
    };
    const handleMoveEnd = (event: MapLibreEvent) => onMoveEndRef.current?.(instance, event);
    const handleError = (event: maplibregl.ErrorEvent) => {
      if (onErrorRef.current) {
        onErrorRef.current(event);
        return;
      }
      const error = event.error as { message?: string; url?: string } | undefined;
      // VWorld keys are embedded in tile URLs. Never log the raw URL.
      console.warn("[VWorldMapView]", error?.message ?? "map error", redactVWorldUrl(error?.url) ?? "");
    };
    instance.once("load", handleLoad);
    instance.on("moveend", handleMoveEnd);
    instance.on("error", handleError);

    const controls: maplibregl.IControl[] = [];
    if (navigation) {
      const control = new maplibregl.NavigationControl({ showCompass: false });
      instance.addControl(control, "top-right");
      controls.push(control);
    }
    if (scale) {
      const control = new maplibregl.ScaleControl({ maxWidth: 150, unit: "metric" });
      instance.addControl(control, "bottom-right");
      controls.push(control);
    }
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => instance.resize());
    resizeObserver?.observe(container);

    return () => {
      resizeObserver?.disconnect();
      instance.off("moveend", handleMoveEnd);
      instance.off("error", handleError);
      controls.forEach((control) => {
        try { instance.removeControl(control); } catch { /* map is already tearing down */ }
      });
      instance.remove();
      mapRef.current = null;
      setLoaded(false);
      setMap(null);
    };
  }, [initialOptions, navigation, scale]);

  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    map.setStyle(buildVWorldStyle(apiKey, layerType));
    map.setMaxZoom(Math.min(maxZoom, getVWorldMaxZoom(layerType)));
  }, [apiKey, layerType, map, maxZoom]);

  useEffect(() => {
    if (!map) return;
    map.setMinZoom(minZoom);
    map.setMaxZoom(Math.min(maxZoom, getVWorldMaxZoom(layerType)));
  }, [map, minZoom, maxZoom, layerType]);

  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    const current = map.getCenter();
    if (current.lng === center[0] && current.lat === center[1] && map.getZoom() === zoom) return;
    map.easeTo({ center, zoom, duration: 350 });
  }, [map, center, zoom]);

  const configured = isVWorldApiKeyConfigured(apiKey);
  return (
    <VWorldMapContext.Provider value={map}>
      <div
        ref={containerRef}
        aria-label="VWorld 날씨 지도"
        className={className}
        data-vworld-basemap={configured ? layerType : "fallback"}
        data-vworld-key={configured ? "configured" : "missing"}
        role="application"
        style={style}
      />
      {map && loaded ? children : null}
    </VWorldMapContext.Provider>
  );
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

/** React portal marker primitive matching maplibre-vworld-react's Marker. */
export function VWorldMarker({
  lngLat,
  anchor = "center",
  offset,
  className,
  selected = false,
  ariaLabel,
  title,
  onClick,
  children,
}: VWorldMarkerProps) {
  const map = useVWorldMap();
  const markerRef = useRef<MapLibreMarker | null>(null);
  const lngLatRef = useRef(lngLat);
  lngLatRef.current = lngLat;
  const onClickRef = useRef(onClick);
  onClickRef.current = onClick;
  const previousClassNameRef = useRef<string | undefined>(undefined);
  const longitude = lngLat[0];
  const latitude = lngLat[1];
  const element = useMemo<HTMLDivElement | null>(() => {
    if (typeof document === "undefined") return null;
    return document.createElement("div");
  }, []);

  useEffect(() => {
    if (!map || !element) return;
    const marker = new maplibregl.Marker({ element, anchor, offset })
      .setLngLat(lngLatRef.current)
      .addTo(map);
    const handleClick = (event: MouseEvent) => {
      event.stopPropagation();
      onClickRef.current?.(event, marker);
    };
    element.addEventListener("click", handleClick);
    markerRef.current = marker;
    return () => {
      element.removeEventListener("click", handleClick);
      marker.remove();
      markerRef.current = null;
    };
  }, [map, element, anchor, offset]);

  useEffect(() => {
    markerRef.current?.setLngLat([longitude, latitude]);
  }, [longitude, latitude]);

  useEffect(() => {
    if (!element) return;
    // MapLibre owns the root classes (including the anchor/covered tokens)
    // and its stylesheet owns the projection transform. Update only the
    // consumer tokens so a state change cannot erase those classes.
    const previousTokens = previousClassNameRef.current
      ? previousClassNameRef.current.split(/\s+/).filter(Boolean)
      : [];
    const nextTokens = className ? className.split(/\s+/).filter(Boolean) : [];
    const nextSet = new Set(nextTokens);
    for (const token of previousTokens) {
      if (!nextSet.has(token)) element.classList.remove(token);
    }
    const previousSet = new Set(previousTokens);
    for (const token of nextTokens) {
      if (!previousSet.has(token)) element.classList.add(token);
    }
    previousClassNameRef.current = className;
    element.dataset.selected = String(selected);
    if (ariaLabel) element.setAttribute("aria-label", ariaLabel);
    else element.removeAttribute("aria-label");
    if (title) element.title = title;
    else element.removeAttribute("title");
  }, [element, className, selected, ariaLabel, title]);

  return element ? createPortal(children, element) : null;
}

export type VWorldWeatherMarkerProps = Omit<VWorldMarkerProps, "children"> & {
  temperature: number | null;
  condition: WeatherCondition;
  alertCount?: number;
};

/**
 * Weather marker from maplibre-vworld-react's WeatherMarker model, adapted to
 * the admin's data contract. It uses a deterministic pill/pin visual and
 * keeps the alert count visible without putting provider keys in the DOM.
 */
export function VWorldWeatherMarker({
  temperature,
  condition,
  alertCount = 0,
  ...markerProps
}: VWorldWeatherMarkerProps) {
  const icon: Record<WeatherCondition, string> = {
    sunny: "☀️",
    cloudy: "☁️",
    rainy: "🌧️",
    snowy: "❄️",
    storm: "⚡",
  };
  const label: Record<WeatherCondition, string> = {
    sunny: "맑음",
    cloudy: "구름",
    rainy: "비",
    snowy: "눈",
    storm: "뇌우",
  };
  return (
    <VWorldMarker
      {...markerProps}
      // The expanded weather marker is a pill, not a pointed pin. Its
      // geographic coordinate must therefore be its visual centre. Callers
      // can still opt into `anchor="bottom"` when providing a pointed
      // marker shape.
      anchor={markerProps.anchor ?? "center"}
      className={`weather-marker vworld-weather-marker weather-marker-${condition}${markerProps.selected ? " selected" : ""}`}
    >
      <button
        aria-label={markerProps.ariaLabel}
        aria-pressed={markerProps.selected}
        className="vworld-weather-marker-button"
        title={markerProps.title}
        type="button"
      >
        <span aria-hidden="true" className="vworld-weather-marker-icon">{icon[condition]}</span>
        <span className="vworld-weather-marker-copy">
          <strong>{temperature === null ? "—" : `${Math.round(temperature)}°`}</strong>
          <small>{label[condition]}</small>
        </span>
        {alertCount > 0 ? <span aria-label={`특보 ${alertCount}건`} className="weather-marker-badge">{alertCount}</span> : null}
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

function weatherConditionIcon(condition: WeatherCondition): string {
  return {
    sunny: "☀️",
    cloudy: "☁️",
    rainy: "🌧️",
    snowy: "❄️",
    storm: "⚡",
  }[condition];
}

function weatherConditionLabel(condition: WeatherCondition): string {
  return {
    sunny: "맑음",
    cloudy: "구름",
    rainy: "비",
    snowy: "눈",
    storm: "뇌우",
  }[condition];
}

function createWeatherClusterPointElement(
  marker: WeatherClusterMarker,
  onClick: () => void,
): HTMLButtonElement {
  const element = document.createElement("button");
  element.type = "button";
  element.className = `weather-marker vworld-weather-marker vworld-weather-marker-button weather-marker-${marker.condition}${marker.selected ? " selected" : ""}`;
  element.setAttribute("aria-label", marker.ariaLabel);
  element.setAttribute("aria-pressed", String(marker.selected === true));
  if (marker.title) element.title = marker.title;
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
  element.append(icon, copy);
  if ((marker.alertCount ?? 0) > 0) {
    const badge = document.createElement("span");
    badge.className = "weather-marker-badge";
    badge.textContent = String(marker.alertCount);
    badge.setAttribute("aria-label", `특보 ${marker.alertCount}건`);
    element.append(badge);
  }
  element.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick();
  });
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
  dataRef.current = data;
  const markerByIdRef = useRef(new Map<string, WeatherClusterMarker>());
  markerByIdRef.current = new Map(markers.map((marker) => [marker.id, marker]));

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
            markerPool.set(id, new maplibregl.Marker({ element }).setLngLat(coordinates));
            markerKeys.set(id, clusterKey);
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
          removeMarker(id);
          const element = createWeatherClusterPointElement(marker, () => {
            markerByIdRef.current.get(markerId)?.onClick?.();
          });
          markerPool.set(id, new maplibregl.Marker({ element }).setLngLat(coordinates));
          markerKeys.set(id, key);
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
