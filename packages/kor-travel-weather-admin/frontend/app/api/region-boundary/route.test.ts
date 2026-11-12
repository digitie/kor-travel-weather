import { NextRequest } from "next/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GET } from "./route";
import geoContract from "@/lib/geo-boundary-contract.json";

const response = (data: unknown) => new Response(JSON.stringify(data));
const request = (longitude = 127) => new NextRequest(`http://weather.test/api/region-boundary?lat=37&lon=${longitude}&level=emd`);
beforeEach(() => { vi.stubEnv("WEATHER_GEO_INTERNAL_URL", "http://geo.test"); vi.stubEnv("WEATHER_GEO_API_KEY", "test-only-non-secret"); });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });
it("실제 Geo OpenAPI의 추가 필드 금지와 size 계약으로 전송한다", async () => {
  const reverse = { candidates: [{ source: "local", region: { sido: "경기도", sigungu: "테스트시", bjd_cd: "4111012300" } }] };
  vi.stubGlobal("fetch", vi.fn(async (url: URL, init: RequestInit) => {
    const search = url.pathname.endsWith("search");
    const schema = (search ? geoContract.schemas.search : geoContract.schemas.reverse);
    const body = JSON.parse(init.body as string);
    expect(schema.additionalProperties).toBe(false);
    for (const key of Object.keys(body)) expect(Object.keys(schema.properties)).toContain(key);
    for (const key of schema.required) expect(body).toHaveProperty(key);
    if (search) { expect(body.size).toBe(10); expect(body.type).toBe("district"); }
    return response(search ? { candidates: [] } : reverse);
  }));
  expect((await GET(request(127.3))).status).toBe(200);
});
it("정확한 법정 코드의 실제 경계만 캐시하고 키는 헤더 외에 노출하지 않는다", async () => {
  const region = { sido: "경기도", sigungu: "테스트시", sig_cd: "41110", bjd_cd: "4111012300" };
  const upstream = vi.fn().mockResolvedValueOnce(response({ candidates: [{ source: "local", region }] }))
    .mockResolvedValueOnce(response({ candidates: [{ source: "local", match_kind: "region", region: { ...region, bjd_cd: "41110123" },
      geometry: { kind: "region", crs: "EPSG:4326", source_table: "tl_scco_emd", geojson: { type: "Polygon", coordinates: [[[127,37],[127.1,37],[127.1,37.1],[127,37]]] } } }] }));
  vi.stubGlobal("fetch", upstream);
  const result = await GET(request());
  expect(result.status).toBe(200);
  expect((await result.json()).data.code).toBe("41110123");
  expect(upstream).toHaveBeenCalledTimes(2);
  expect(upstream.mock.calls[0][0].toString()).toBe("http://geo.test/v2/reverse");
  expect(upstream.mock.calls[0][1].headers["X-KTG-API-Key"]).toBe("test-only-non-secret");
  expect(upstream.mock.calls[1][1].body).not.toContain("test-only-non-secret");
  expect((await GET(request())).status).toBe(200);
  expect(upstream).toHaveBeenCalledTimes(2);
});
it("미설정/잘못된 좌표는 upstream에 보내지 않고 실패 원문은 노출하지 않는다", async () => {
  const upstream = vi.fn().mockRejectedValue(new Error("private upstream URL/key")); vi.stubGlobal("fetch", upstream);
  expect((await GET(request(180))).status).toBe(400);
  expect((await GET(request(132))).status).toBe(400);
  expect((await GET(new NextRequest("http://weather.test/api/region-boundary?lat=39&lon=127&level=sido"))).status).toBe(400);
  vi.stubEnv("WEATHER_GEO_API_KEY", "");
  expect((await GET(request(128))).status).toBe(503);
  expect(upstream).not.toHaveBeenCalled();
  vi.stubEnv("WEATHER_GEO_API_KEY", "test-only-non-secret");
  const result = await GET(request(128));
  expect(result.status).toBe(502);
  expect(await result.text()).not.toContain("private upstream");
});
it("동시 원천 조회는 네 건으로 제한하고 실패 후 슬롯을 반환한다", async () => {
  const pending: ((value: Response) => void)[] = [];
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => pending.push(resolve))));
  const calls = [127.01, 127.02, 127.03, 127.04].map(lon => GET(request(lon)));
  expect(pending.length).toBe(4);
  expect((await GET(request(127.05))).status).toBe(503);
  pending.forEach(resolve => resolve(response({ candidates: [] })));
  expect((await Promise.all(calls)).every(result => result.status === 200)).toBe(true);
  const next = GET(request(127.06));
  expect(pending.length).toBe(5);
  pending.at(-1)!(response({ candidates: [] }));
  expect((await next).status).toBe(200);
});
it("만료된 같은 항목의 동시 갱신 뒤에도 실제 16MiB 용량 상한을 유지한다", async () => {
  let now = Date.now(); vi.spyOn(Date, "now").mockImplementation(() => now);
  let name = "x".repeat(2 * 1024 * 1024);
  const upstream = vi.fn(async (url: URL) => response(url.pathname.endsWith("reverse")
    ? { candidates: [{ source: "local", region: { sido: "경기도", sigungu: "테스트시", bjd_cd: "4111012300" } }] }
    : { candidates: [{ source: "local", match_kind: "region", region: { bjd_cd: "41110123" }, address: { full: name },
      geometry: { kind: "region", crs: "EPSG:4326", source_table: "tl_scco_emd", geojson: { type: "Polygon", coordinates: [[[127,37],[127.1,37],[127.1,37.1],[127,37]]] } } }] }));
  vi.stubGlobal("fetch", upstream);
  expect((await GET(request(127.4))).status).toBe(200);
  now += 6 * 60 * 60 * 1000 + 1; name = "x".repeat(1024 * 1024);
  expect((await Promise.all([GET(request(127.4)), GET(request(127.4))])).every(value => value.status === 200)).toBe(true);
  for (let index = 1; index <= 15; index++) expect((await GET(request(128 + index / 100))).status).toBe(200);
  const count = upstream.mock.calls.length;
  await GET(request(127.4));
  // 가장 오래된1MiB 항목은 실제 용량이16MiB를 넘기 전에 퇴출되어 재조회된다.
  expect(upstream.mock.calls.length).toBe(count + 2);
});
