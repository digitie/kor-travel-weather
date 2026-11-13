import type { Feature, Polygon, MultiPolygon } from "geojson";

export type BoundaryLevel = "sido" | "sigungu" | "emd" | "li";
export type WeatherBoundary = {
  level: BoundaryLevel;
  code: string;
  name: string;
  bbox: [number, number, number, number];
  feature: Feature<Polygon | MultiPolygon>;
};
export type BoundaryRequest = { latitude: number; longitude: number; level: BoundaryLevel };
export function boundaryForSelection(request: BoundaryRequest | null, selected: { latitude: number; longitude: number } | null | undefined, detailOpen: boolean, mode: string): BoundaryRequest | null {
  if (!detailOpen || mode !== "map" || !request || !selected) return null;
  return request.latitude === selected.latitude && request.longitude === selected.longitude ? request : null;
}
export function boundaryLevel(zoom: number): BoundaryLevel {
  return zoom < 8 ? "sido" : zoom < 12 ? "sigungu" : zoom < 15 ? "emd" : "li";
}

const tables = { sido: "tl_scco_ctprvn", sigungu: "tl_scco_sig", emd: "tl_scco_emd", li: "tl_scco_li" };
const digits = { sido: 2, sigungu: 5, emd: 8, li: 10 };

/** Geo의 법정 코드만 사용한다. Weather의 region_code는 기상 예보 코드다. */
export function regionLookup(payload: unknown, level: BoundaryLevel): { code: string; query: string; hint: Record<string, string> } | null {
  if (!payload || typeof payload !== "object" || !("candidates" in payload) || !Array.isArray(payload.candidates)) return null;
  const regions = payload.candidates.filter(item => item?.source === "local" && item.region?.sido)
    .map(item => item.region);
  const keys = new Map<string, { code: string; query: string; hint: Record<string, string> }>();
  for (const region of regions) {
    const raw = region.bjd_cd ?? region.sig_cd;
    if (typeof raw !== "string" || !/^\d{2}(?:\d{3})?(?:\d{3}(?:\d{2})?)?$/u.test(raw) || raw.length < digits[level]) continue;
    const code = raw.slice(0, digits[level]);
    if (level === "li" && code.endsWith("00")) continue;
    const query = level === "sido" ? region.sido : [region.sido, region.sigungu].filter(Boolean).join(" ");
    const hint: Record<string, string> = level === "sido" || level === "sigungu"
      ? { sig_cd: code } : { bjd_cd: level === "emd" ? `${code}00` : code };
    keys.set(code, { code, query, hint });
  }
  return keys.size === 1 ? [...keys.values()][0] : null;
}

/** 실제 Polygon/MultiPolygon만 허용하며 bbox도 좌표에서 검산한다. 사각형을 경계로 꾸미지 않는다. */
export function parseBoundary(payload: unknown, level: BoundaryLevel, code: string): WeatherBoundary | null {
  if (!payload || typeof payload !== "object" || !("candidates" in payload) || !Array.isArray(payload.candidates)) return null;
  const matching = payload.candidates.filter(item => item?.source === "local" && item.match_kind === "region"
    && item.geometry?.kind === "region" && item.geometry.crs === "EPSG:4326"
    && item.geometry.source_table === tables[level]
    && (level === "sido" || level === "sigungu" ? item.region?.sig_cd : item.region?.bjd_cd)?.slice(0, digits[level]) === code);
  if (matching.length !== 1) return null;
  const candidate = matching[0];
  const geometry = candidate.geometry.geojson;
  if (!geometry || !["Polygon", "MultiPolygon"].includes(geometry.type) || !Array.isArray(geometry.coordinates)) return null;
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  let count = 0;
  let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity;
  if (!polygons.length) return null;
  for (const polygon of polygons) {
    if (!Array.isArray(polygon) || !polygon.length) return null;
    for (const ring of polygon) {
      if (!Array.isArray(ring) || ring.length < 4) return null;
      for (const point of ring) {
        if (++count > 100_000 || !Array.isArray(point) || point.length < 2
          || !Number.isFinite(point[0]) || !Number.isFinite(point[1])
          || point[0] < 124 || point[0] > 132 || point[1] < 32 || point[1] > 44) return null;
        west = Math.min(west, point[0]); east = Math.max(east, point[0]);
        south = Math.min(south, point[1]); north = Math.max(north, point[1]);
      }
      if (ring[0][0] !== ring.at(-1)[0] || ring[0][1] !== ring.at(-1)[1]) return null;
    }
  }
  if (west >= east || south >= north) return null;
  return { level, code, name: candidate.address?.full ?? code, bbox: [west, south, east, north],
    feature: { type: "Feature", properties: { code }, geometry } };
}
