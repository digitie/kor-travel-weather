import type { Location } from "./api";

const REGION_SHORT_NAMES: Readonly<Record<string, string>> = {
  충청북도: "충북", 충청남도: "충남", 경상북도: "경북", 경상남도: "경남",
  전라북도: "전북", 전북특별자치도: "전북", 전라남도: "전남",
};

/** 표시만 축약한다. 원래 위치명·주소·지역별 그룹 키는 변경하지 않는다. */
export function abbreviateWeatherRegionName(name: string): string {
  return name.trim().split(/\s+/u).map(part => REGION_SHORT_NAMES[part]
    ?? part.replace(/(?:특별자치도|특별자치시|특별시|광역시|자치도|자치시|시|도)$/u, ""))
    .join(" ");
}

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
