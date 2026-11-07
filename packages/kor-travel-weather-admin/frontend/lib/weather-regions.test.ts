import { describe, expect, it } from "vitest";
import type { Location } from "./api";
import { weatherRepresentatives, weatherRegionLevel } from "./weather-regions";

const point = (id: string, longitude: number, district: string): Location => ({location_id:id,name:id,longitude,latitude:37,nx:null,ny:null,region_code:null,enabled:true,metadata:{},sido_name:"경기도",sigungu_name:district});
describe("행정구역 대표 날씨", () => {
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
