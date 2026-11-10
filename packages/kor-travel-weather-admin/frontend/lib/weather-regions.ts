import type { Location } from "./api";

export function weatherRegionLevel(zoom: number) {
  return zoom < 8 ? "sido" : zoom < 12 ? "sigungu" : null;
}

/** 행정구역별 중심에 가장 가까운 실제 지점을 고른다. 기상 코드를 평균내지 않는다. */
export function weatherRepresentatives(locations: Location[], zoom: number): { location: Location; label: string }[] {
  const level = weatherRegionLevel(zoom);
  if (!level) return locations.map(location => ({ location, label: location.name }));
  const groups = new Map<string, Location[]>();
  for (const location of locations) {
    if (!location.sido_name || (level === "sigungu" && !location.sigungu_name)) continue;
    const label = [location.sido_name, ...(level === "sigungu" ? [location.sigungu_name] : [])].join(" ");
    const group = groups.get(label) ?? []; group.push(location); groups.set(label, group);
  }
  return [...groups].map(([label, group]) => {
    const lon = group.reduce((n, p) => n + p.longitude, 0) / group.length;
    const lat = group.reduce((n, p) => n + p.latitude, 0) / group.length;
    const distance = (p: Location) => (p.longitude - lon) ** 2 + (p.latitude - lat) ** 2;
    return { label, location: [...group].sort((a, b) => distance(a) - distance(b) || a.location_id.localeCompare(b.location_id))[0] };
  }).sort((a, b) => a.label.localeCompare(b.label));
}
