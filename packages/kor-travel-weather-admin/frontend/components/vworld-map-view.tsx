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

export type WeatherCondition = "sunny" | "cloudy" | "rainy" | "snowy" | "storm";

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
