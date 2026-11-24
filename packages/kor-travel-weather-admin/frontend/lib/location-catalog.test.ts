import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { catalogSourceKey, locationCatalogQuery, locationCatalogQueryKey } from "./location-catalog";

afterEach(() => vi.unstubAllGlobals());

function catalogFetch() {
  const seen: (string | null)[] = [];
  const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
    const etag = ((init.headers ?? {}) as Record<string, string>)["if-none-match"] ?? null;
    seen.push(etag);
    if (etag === 'W/"p0"') return new Response(null, { status: 304, headers: { etag } });
    return new Response(JSON.stringify({ data: [{ location_id: "a" }], meta: { page: { total: 1 } } }), {
      headers: { "content-type": "application/json", etag: 'W/"p0"' },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, seen };
}

describe("위치 카탈로그 화면 간 캐시(B12)", () => {
  it("query key는 지도 필터의 sourceKey와 같은 문자열이다", () => {
    const sources = ["python-kma-api"];
    const kinds = ["alert" as const];
    expect(catalogSourceKey(sources, kinds)).toBe(JSON.stringify([sources, kinds]));
    expect(catalogSourceKey(null, undefined)).toBe(JSON.stringify([null, undefined]));
    expect(locationCatalogQueryKey("k")).toEqual(["weather-location-catalog", "k"]);
  });

  it("신선한 동안은 다시 받지 않고, 다시 받을 때는 지난 ETag로 304를 받아 같은 객체를 쓴다", async () => {
    const { fetchMock, seen } = catalogFetch();
    const client = new QueryClient();
    const options = locationCatalogQuery(client, null, undefined);
    const first = await client.fetchQuery(options);
    expect(first.locations).toEqual([{ location_id: "a" }]);
    // staleTime 안의 재진입·필터 복귀는 네트워크 없이 캐시를 쓴다.
    expect(await client.fetchQuery(options)).toBe(first);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // 오래된 뒤 다시 받으면 페이지 ETag를 보내고 304면 지난 카탈로그 객체를 그대로 유지한다.
    const again = await client.fetchQuery({ ...options, staleTime: 0 });
    expect(seen).toEqual([null, 'W/"p0"']);
    expect(again).toBe(first);
    client.clear();
  });

  it("조건이 다르면 다른 key로 따로 받는다", async () => {
    const { fetchMock } = catalogFetch();
    const client = new QueryClient();
    await client.fetchQuery(locationCatalogQuery(client, null, undefined));
    await client.fetchQuery(locationCatalogQuery(client, ["python-kma-api"], undefined));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const url = new URL(String(fetchMock.mock.calls[1][0]), "http://localhost");
    expect(url.searchParams.getAll("provider")).toEqual(["python-kma-api"]);
    client.clear();
  });
});
