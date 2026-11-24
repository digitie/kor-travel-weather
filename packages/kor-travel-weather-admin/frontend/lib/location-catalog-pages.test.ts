import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchLocationCatalog,
  LOCATION_PAGE_CONCURRENCY,
  LOCATION_PAGE_SIZE,
  type LocationCatalog,
} from "./api";

afterEach(() => vi.unstubAllGlobals());

type Call = { offset: number; etag: string | null; signal?: AbortSignal; resolve: (response: Response) => void };

function rows(offset: number, count: number) {
  return Array.from({ length: count }, (_, i) => ({ location_id: `l-${offset + i}` }));
}

function page(offset: number, total: number, { etag = `W/"p${offset}"`, count }: { etag?: string; count?: number } = {}) {
  const size = count ?? Math.min(LOCATION_PAGE_SIZE, total - offset);
  return new Response(JSON.stringify({ data: rows(offset, size), meta: { page: { total } } }), {
    headers: { "content-type": "application/json", etag },
  });
}

/** 요청을 잡아 두고 시험이 응답 순서를 정한다. */
function captureFetch() {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit) => new Promise<Response>((resolve, reject) => {
    const query = new URL(url, "http://localhost").searchParams;
    const headers = (init.headers ?? {}) as Record<string, string>;
    const call: Call = { offset: Number(query.get("offset")), etag: headers["if-none-match"] ?? null,
      signal: init.signal ?? undefined, resolve };
    init.signal?.addEventListener("abort", () => reject(new DOMException("취소", "AbortError")), { once: true });
    calls.push(call);
  })));
  return calls;
}

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe("위치 카탈로그 병렬 수신(B12)", () => {
  it("첫 페이지 total로 나머지를 동시에 받고 페이지 순서대로 합친다", async () => {
    const calls = captureFetch();
    const pending = fetchLocationCatalog({}, undefined);
    await flush();
    expect(calls.map((call) => call.offset)).toEqual([0]);
    calls[0].resolve(page(0, 2500)); await flush();
    // 첫 페이지 뒤 두 페이지를 함께 보낸다.
    expect(calls.map((call) => call.offset)).toEqual([0, 1000, 2000]);
    calls[2].resolve(page(2000, 2500)); calls[1].resolve(page(1000, 2500));
    const catalog = await pending;
    expect(catalog.locations).toHaveLength(2500);
    expect(catalog.locations[0].location_id).toBe("l-0");
    expect(catalog.locations[1000].location_id).toBe("l-1000");
    expect(catalog.locations.at(-1)?.location_id).toBe("l-2499");
    expect(catalog.pages.map((item) => item.etag)).toEqual(['W/"p0"', 'W/"p1000"', 'W/"p2000"']);
  });

  it(`동시에 진행하는 나머지 페이지는 ${LOCATION_PAGE_CONCURRENCY}개를 넘지 않는다`, async () => {
    const calls = captureFetch();
    const total = 6500;
    const pending = fetchLocationCatalog({ providers: ["python-kma-api"] }, undefined);
    await flush(); calls[0].resolve(page(0, total)); await flush();
    let maximum = 0;
    const answered = new Set<number>([0]);
    while (answered.size < 7) {
      const open = calls.filter((call) => !answered.has(call.offset));
      maximum = Math.max(maximum, open.length);
      open[0].resolve(page(open[0].offset, total)); answered.add(open[0].offset);
      await flush();
    }
    expect(maximum).toBe(LOCATION_PAGE_CONCURRENCY);
    expect((await pending).locations).toHaveLength(total);
    expect(calls).toHaveLength(7);
  });

  it("지난 ETag를 보내 모든 페이지가 304면 지난 카탈로그 객체를 그대로 돌려준다", async () => {
    let calls = captureFetch();
    let pending = fetchLocationCatalog({}, undefined);
    await flush(); calls[0].resolve(page(0, 1500)); await flush(); calls[1].resolve(page(1000, 1500));
    const previous = await pending;

    calls = captureFetch();
    pending = fetchLocationCatalog({}, undefined, previous);
    await flush();
    expect(calls[0].etag).toBe('W/"p0"');
    calls[0].resolve(new Response(null, { status: 304, headers: { etag: 'W/"p0"' } })); await flush();
    expect(calls[1].etag).toBe('W/"p1000"');
    calls[1].resolve(new Response(null, { status: 304 }));
    expect(await pending).toBe(previous);
  });

  it("바뀐 페이지만 새로 받고 304 페이지의 행은 재사용한다", async () => {
    const previous: LocationCatalog = {
      pages: [
        { offset: 0, etag: 'W/"p0"', total: 1500, data: rows(0, 1000) as never },
        { offset: 1000, etag: 'W/"p1000"', total: 1500, data: rows(1000, 500) as never },
      ],
      locations: [],
    };
    const calls = captureFetch();
    const pending = fetchLocationCatalog({}, undefined, previous);
    await flush(); calls[0].resolve(new Response(null, { status: 304 })); await flush();
    calls[1].resolve(page(1000, 1500, { etag: 'W/"p1000-new"' }));
    const next = await pending;
    expect(next).not.toBe(previous);
    expect(next.pages[0]).toBe(previous.pages[0]);
    expect(next.pages[1].etag).toBe('W/"p1000-new"');
    expect(next.locations).toHaveLength(1500);
  });

  it("받는 사이 total이 바뀌면 처음부터 한 페이지씩 다시 받는다", async () => {
    const calls = captureFetch();
    const pending = fetchLocationCatalog({}, undefined);
    await flush(); calls[0].resolve(page(0, 1500)); await flush();
    // 두 번째 페이지가 다른 total을 알린다(사이에 위치가 추가됐다).
    calls[1].resolve(page(1000, 1600, { count: 600 })); await flush();
    expect(calls.map((call) => [call.offset, call.etag])).toEqual([[0, null], [1000, null], [0, null]]);
    calls[2].resolve(page(0, 1600)); await flush();
    calls[3].resolve(page(1000, 1600));
    const catalog = await pending;
    expect(catalog.locations).toHaveLength(1600);
  });

  it("한 페이지가 실패하면 진행 중인 다른 페이지를 끊고 실패를 돌려준다", async () => {
    const calls = captureFetch();
    const pending = fetchLocationCatalog({}, undefined);
    const outcome = pending.catch((error: unknown) => error);
    await flush(); calls[0].resolve(page(0, 3000)); await flush();
    calls[1].resolve(new Response(JSON.stringify({ detail: "원천 실패" }), {
      status: 500, headers: { "content-type": "application/json" },
    }));
    expect(await outcome).toBeInstanceOf(Error);
    expect(calls[2].signal?.aborted).toBe(true);
    expect(calls).toHaveLength(3);
  });

  it("호출자 취소는 나머지 페이지 요청에도 전달된다", async () => {
    const calls = captureFetch();
    const controller = new AbortController();
    const pending = fetchLocationCatalog({}, controller.signal).catch((error: unknown) => error);
    await flush(); calls[0].resolve(page(0, 3000)); await flush();
    controller.abort();
    expect(await pending).toMatchObject({ name: "AbortError" });
    expect(calls.slice(1).every((call) => call.signal?.aborted)).toBe(true);
  });
});
