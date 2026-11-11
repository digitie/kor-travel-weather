import type { StyleSpecification } from "maplibre-gl";

import {
  createVWorldStyle, getVWorldTileUrl as upstreamTileUrl,
  getVWorldMaxZoom as upstreamMaxZoom, redactVWorldUrl, type VWorldMapType,
} from "vworld-map-core";
export { redactVWorldUrl };
export type VWorldLayerType = "Base" | "gray" | "midnight" | "Hybrid" | "Satellite";
const mapType: Record<VWorldLayerType, VWorldMapType> = {
  Base: "base", gray: "gray", midnight: "midnight", Hybrid: "hybrid", Satellite: "satellite",
};
export function getVWorldMaxZoom(layerType: VWorldLayerType) {
  return upstreamMaxZoom(mapType[layerType]);
}

/** Return true only for a usable browser tile key. */
export function isVWorldApiKeyConfigured(apiKey: string | undefined): apiKey is string {
  const value = apiKey?.trim();
  return Boolean(value && value !== "CHANGE_ME" && value !== "change-me");
}

/**
 * Build the VWorld WMTS URL used by maplibre-vworld-react's web adapter.
 * The key is public by design (it is part of each tile request), but it is
 * still trimmed/encoded so an env-injected newline cannot invalidate tiles.
 */
export function getVWorldTileUrl(apiKey: string, layerType: VWorldLayerType): string {
  return upstreamTileUrl(apiKey, mapType[layerType]);
}

/** Build a VWorld raster style, or a neutral MapLibre fallback without a key. */
export function buildVWorldStyle(
  apiKey: string | undefined,
  layerType: VWorldLayerType = "Base",
): StyleSpecification {
  if (!isVWorldApiKeyConfigured(apiKey)) {
    return {
      version: 8,
      sources: {},
      layers: [{
        id: "vworld-key-missing",
        type: "background",
        paint: { "background-color": "#e9eef4" },
      }],
    };
  }

  return createVWorldStyle(apiKey, mapType[layerType]);
}
