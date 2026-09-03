import type { StyleSpecification } from "maplibre-gl";

// The URL/style boundary follows digitie's maplibre-vworld-react repository
// (vworld-map-core + vworld-map-web).  Keeping this small web adapter local
// makes the admin image reproducible while preserving the upstream React
// component contract and VWorld's {z}/{y}/{x} WMTS addressing.
const VWORLD_ATTRIBUTION = "공간정보 오픈플랫폼 브이월드";
const VWORLD_WMTS_PATH = /(\/req\/wmts\/1\.0\.0\/)([^/?#]+)(\/)/;

export type VWorldLayerType = "Base" | "gray" | "midnight" | "Hybrid" | "Satellite";

const LAYER_NAME: Record<VWorldLayerType, string> = {
  Base: "Base",
  gray: "white",
  midnight: "midnight",
  Hybrid: "Hybrid",
  Satellite: "Satellite",
};

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
  const extension = layerType === "Satellite" ? "jpeg" : "png";
  return `https://api.vworld.kr/req/wmts/1.0.0/${encodeURIComponent(apiKey.trim())}/${LAYER_NAME[layerType]}/{z}/{y}/{x}.${extension}`;
}

export function getVWorldMaxZoom(layerType: VWorldLayerType): number {
  return layerType === "Hybrid" || layerType === "Satellite" ? 18 : 19;
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

  const sources: StyleSpecification["sources"] = {};
  const layers: StyleSpecification["layers"] = [];
  const addRaster = (sourceId: string, layerId: string, sourceLayer: VWorldLayerType) => {
    sources[sourceId] = {
      type: "raster",
      tiles: [getVWorldTileUrl(apiKey, sourceLayer)],
      tileSize: 256,
      attribution: VWORLD_ATTRIBUTION,
      maxzoom: getVWorldMaxZoom(sourceLayer),
    };
    layers.push({ id: layerId, type: "raster", source: sourceId, minzoom: 0 });
  };

  if (layerType === "Hybrid") addRaster("vworld-satellite", "vworld-satellite-layer", "Satellite");
  addRaster("vworld-base", "vworld-base-layer", layerType);
  return { version: 8, sources, layers };
}

/** Redact the public VWorld key before sending tile URLs to logs/errors. */
export function redactVWorldUrl(url: string | undefined): string | undefined {
  return url?.replace(VWORLD_WMTS_PATH, "$1***$3");
}
