"use client";

import { useEffect, useId, useState } from "react";
import { useVWorldMap } from "./vworld-map-view";
import type { BoundaryRequest, WeatherBoundary } from "@/lib/weather-boundary";

export function WeatherRegionBoundary({ request }: { request: BoundaryRequest | null }) {
  const map = useVWorldMap();
  const id = `weather-boundary-${useId().replaceAll(":", "")}`;
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!map || !request) return;
    const controller = new AbortController();
    let value: WeatherBoundary | null = null;
    let manualMove = false;
    const move = (event: { originalEvent?: unknown }) => { if (event.originalEvent) manualMove = true; };
    const remove = () => {
      if (map.getLayer(`${id}-line`)) map.removeLayer(`${id}-line`);
      if (map.getLayer(`${id}-fill`)) map.removeLayer(`${id}-fill`);
      if (map.getSource(id)) map.removeSource(id);
    };
    const draw = () => {
      if (!value || controller.signal.aborted || !map.isStyleLoaded()) return;
      remove();
      map.addSource(id, { type: "geojson", data: value.feature });
      const before = map.getStyle().layers?.find(layer => layer.type === "symbol")?.id;
      map.addLayer({ id: `${id}-fill`, type: "fill", source: id, paint: { "fill-color": "#2563eb", "fill-opacity": 0.1 } }, before);
      map.addLayer({ id: `${id}-line`, type: "line", source: id, paint: { "line-color": "#2563eb", "line-width": 2 } }, before);
    };
    map.on("movestart", move); map.on("style.load", draw);
    setMessage("행정 경계 확인 중");
    const params = new URLSearchParams({ lat: String(request.latitude), lon: String(request.longitude), level: request.level });
    void fetch(`/api/region-boundary?${params}`, { signal: controller.signal, cache: "no-store" })
      .then(async response => {
        const result = await response.json();
        if (controller.signal.aborted) return;
        if (!response.ok || !result.data) { setMessage(result.detail ?? "행정 경계 자료가 없습니다."); return; }
        value = result.data as WeatherBoundary;
        draw(); setMessage(`${value.name} 경계`);
        if (manualMove) return;
        const root = map.getContainer();
        const bounds = root.getBoundingClientRect();
        const { width, height } = bounds;
        if (width < 120 || height < 120) return;
        const mobile = width < 700;
        const detail = root.closest(".weather-region-stage")?.querySelector(".kt-map-detail")?.getBoundingClientRect();
        map.fitBounds([[value.bbox[0], value.bbox[1]], [value.bbox[2], value.bbox[3]]], {
          padding: { top: mobile ? Math.min(height - 100, Math.max(56, detail ? detail.bottom - bounds.top + 12 : height * 0.45)) : 56,
            left: 28, right: mobile ? 28 : Math.min(width - 120, Math.max(28, detail ? bounds.right - detail.left + 12 : Math.min(480, width * 0.45))), bottom: 40 },
          maxZoom: 16, duration: 500,
        });
      }).catch(() => { if (!controller.signal.aborted) setMessage("행정 경계를 읽지 못했습니다."); });
    return () => {
      controller.abort(); map.off("movestart", move); map.off("style.load", draw); remove();
    };
  }, [id, map, request]);
  return request && message ? <span className="weather-boundary-status" role="status">{message}</span> : null;
}
