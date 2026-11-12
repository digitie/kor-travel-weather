import { describe, expect, it } from "vitest";
import type { Location, WeatherRegionProjection } from "./api";
import { abbreviateWeatherRegionName, weatherRepresentatives, weatherRegionLevel } from "./weather-regions";

const point = (id: string, longitude: number, district: string): Location => ({location_id:id,name:id,longitude,latitude:37,nx:null,ny:null,region_code:null,enabled:true,metadata:{},sido_name:"경기도",sigungu_name:district});
describe("행정구역 대표 날씨", () => {
  it("초기 저배율은 전체 카탈로그 없이 저장 대표를 쓰고 상세 축척은 실제 목록을 쓴다", () => {
    const station = point("saved", 127, "수원시");
    const projection: WeatherRegionProjection = {
      ready: true, calculated_at: "2026-11-13T00:00:00Z", items: [
        { level: "sido", region_key: "province", label: "경기도", member_count: 1000, location: station },
        { level: "sigungu", region_key: "district", label: "경기도 수원시", member_count: 100, location: station },
      ],
    };
    expect(weatherRepresentatives([], 7, projection)).toEqual([{ location: station, label: "경기도" }]);
    expect(weatherRepresentatives([], 9, projection)).toEqual([{ location: station, label: "경기도 수원시" }]);
    const live = point("live", 128, "용인시");
    expect(weatherRepresentatives([live], 12, projection)).toEqual([{ location: live, label: "live" }]);
    expect(weatherRepresentatives([live], 7, { ...projection, ready: false })[0].location).toBe(live);
    expect(weatherRepresentatives([live], 7, null)[0].location).toBe(live);
  });
  it.each([
    ["인천광역시", "인천"], ["서울특별시", "서울"], ["파주시", "파주"],
    ["충청북도", "충북"], ["경상북도", "경북"], ["경상남도", "경남"],
    ["전북특별자치도", "전북"], ["강원특별자치도", "강원"],
    ["제주특별자치도 서귀포시", "제주 서귀포"], ["경기도 파주시", "경기 파주"],
    ["세종특별자치시", "세종"], ["중구", "중구"], ["울릉군", "울릉군"],
  ])("%s의 지도 표기는 %s", (name, expected) => {
    expect(abbreviateWeatherRegionName(name)).toBe(expected);
    expect(abbreviateWeatherRegionName(expected)).toBe(expected);
  });
  it("지점을 더하거나 순서를 바꿔도 지역 중심의 실제 지점을 고른다", () => {
    const points=[point("a",126,"수원시"),point("b",127,"수원시"),point("c",129,"용인시")];
    expect(weatherRepresentatives(points,7)[0].location.location_id).toBe("b");
    expect(weatherRepresentatives([...points].reverse(),7)).toEqual(weatherRepresentatives(points,7));
    expect(weatherRepresentatives(points,8)).toHaveLength(2);
    expect(weatherRegionLevel(11.99)).toBe("sigungu");expect(weatherRegionLevel(12)).toBeNull();
    expect(weatherRepresentatives(points,12)).toHaveLength(3);
  });
  it("주소 미확인 지점을 이름으로 추정하지 않는다", () => {
    expect(weatherRepresentatives([{...point("x",127,"수원시"),sido_name:null}],7)).toEqual([]);
  });
});
