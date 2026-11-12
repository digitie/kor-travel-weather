import { describe, expect, it } from "vitest";
import { boundaryLevel, parseBoundary, regionLookup } from "./weather-boundary";

const region = { sido: "경기도", sigungu: "수원시", sig_cd: "41110", bjd_cd: "4111012300" };
const polygon = { type: "Polygon", coordinates: [[[127, 37], [127.1, 37], [127.1, 37.1], [127, 37]]] };
const candidate = { source: "local", match_kind: "region", region: { ...region, bjd_cd: "41110123" },
  address: { full: "경기도 수원시 테스트동" }, geometry: { kind: "region", crs: "EPSG:4326", source_table: "tl_scco_emd", geojson: polygon } };
describe("실제 Geo 행정 경계", () => {
  it("축척 단계와 Geo 법정 코드만 사용하고 기상 코드를 받지 않는다", () => {
    expect([7, 9, 13, 16].map(boundaryLevel)).toEqual(["sido", "sigungu", "emd", "li"]);
    expect(regionLookup({ candidates: [{ source: "local", region }] }, "emd")).toEqual({ code: "41110123", query: "경기도 수원시", hint: { bjd_cd: "4111012300" } });
    expect(regionLookup({ candidates: [{ source: "local", region }] }, "li")).toBeNull();
    expect(regionLookup({ region_code: "11B10101" }, "sigungu")).toBeNull();
    expect(regionLookup({ candidates: [{ source: "vworld", region }] }, "sigungu")).toBeNull();
    expect(regionLookup({ candidates: [{ source: "local", region }, { source: "local", region: { ...region, bjd_cd: "4211012300" } }] }, "emd")).toBeNull();
  });
  it("실제 polygon의 bbox를 계산하며 위조 bbox/건물/다른 단계/중복 후보는 거절한다", () => {
    expect(parseBoundary({ candidates: [candidate] }, "emd", "41110123")?.bbox).toEqual([127, 37, 127.1, 37.1]);
    expect(parseBoundary({ candidates: [candidate] }, "sido", "41")).toBeNull();
    expect(parseBoundary({ candidates: [candidate, candidate] }, "emd", "41110123")).toBeNull();
    expect(parseBoundary({ candidates: [{ ...candidate, geometry: { ...candidate.geometry, kind: "building" } }] }, "emd", "41110123")).toBeNull();
    expect(parseBoundary({ candidates: [{ ...candidate, geometry: { ...candidate.geometry, geojson: { ...polygon, coordinates: [[[127, 37], [128, 38], [129, 39], [128, 38]]] } } }] }, "emd", "41110123")).toBeNull();
  });
});
