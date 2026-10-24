import { afterEach, describe, expect, it, vi } from "vitest";

import { forecastWindowStart, getAllPublicLocations, getPublicLocations, getLatest, getForecast, getMarkerSummaries } from "./api";

function respond(body: string, init: ResponseInit) {
  const fetchMock = vi.fn(async () => new Response(body, init));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request error handling", () => {
  // A short plain-text body is the server's own sentence and says more than the
  // status code (lib/http.ts failureMessage, since 571ec32). What must never
  // reach the reader is the parser's complaint about that sentence.
  it("reports the middleware's own sentence for a plain-text 401", async () => {
    respond("관리자 UI 인증이 필요합니다.", {
      status: 401,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
    // Parsing before checking the status turned this into
    // `SyntaxError: Unexpected token '관' ...`, hiding what the server said.
    const failure = getPublicLocations();
    await expect(failure).rejects.toThrow("관리자 UI 인증이 필요합니다.");
    await expect(failure).rejects.not.toThrow(/token|JSON/);
  });

  it("reports the backend's plain-text body for an unhandled 500", async () => {
    respond("Internal Server Error", {
      status: 500,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
    await expect(getPublicLocations()).rejects.toThrow("Internal Server Error");
  });

  it("falls back to the status for a plain-text body too long to be a sentence", async () => {
    respond("x".repeat(201), {
      status: 500,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
    await expect(getPublicLocations()).rejects.toThrow("요청 실패 (500)");
  });

  it("reports the status when a gateway times out with an HTML body", async () => {
    respond("<html><body><h1>504 Gateway Time-out</h1></body></html>", {
      status: 504,
      headers: { "content-type": "text/html" },
    });
    await expect(getPublicLocations()).rejects.toThrow("요청 실패 (504)");
  });

  it("prefers the RFC7807 detail when the backend sends one", async () => {
    respond(JSON.stringify({ detail: "요청 좌표 주변에 위치가 없습니다." }), {
      status: 404,
      headers: { "content-type": "application/problem+json" },
    });
    await expect(getPublicLocations()).rejects.toThrow("요청 좌표 주변에 위치가 없습니다.");
  });

  it("returns the envelope on success", async () => {
    const envelope = {
      data: [{ location_id: "seoul" }],
      meta: { request_id: "r", generated_at: "t", duration_ms: 1 },
    };
    respond(JSON.stringify(envelope), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    await expect(getPublicLocations()).resolves.toMatchObject({
      data: [{ location_id: "seoul" }],
    });
  });

  it("does not silently succeed on an empty 200 body", async () => {
    respond("", { status: 200, headers: { "content-type": "application/json" } });
    await expect(getPublicLocations()).rejects.toThrow("응답을 해석하지 못했습니다 (200)");
  });
});

describe("forecastWindowStart", () => {
  it("anchors at the top of the current hour", () => {
    expect(forecastWindowStart(new Date("2026-09-08T05:37:42.514Z"))).toBe(
      "2026-09-08T05:00:00.000Z",
    );
  });

  it("returns a timezone-aware ISO-8601 string the API accepts", () => {
    // `from` without a timezone is a 422 on the forecast route.
    expect(forecastWindowStart(new Date("2026-01-01T00:00:00Z"))).toMatch(/Z$/);
  });

  it("does not reach into the past", () => {
    const now = new Date("2026-09-08T05:37:42.514Z");
    expect(new Date(forecastWindowStart(now)).getTime()).toBeGreaterThan(
      now.getTime() - 60 * 60 * 1000,
    );
  });
});

describe("일반 날씨 카탈로그", () => {
  it("모든 페이지에 제공처·weather_only·취소 signal을 유지한다", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn(async (url: string) => {
      const offset = Number(new URL(url, "http://localhost").searchParams.get("offset"));
      return new Response(JSON.stringify({ data: Array.from({ length: offset ? 1 : 1000 },
        (_, i) => ({ location_id: `location-${offset + i}` })), meta: {} }),
      { headers: { "content-type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await getAllPublicLocations(["python-kma-api"], controller.signal, true);
    expect(result).toHaveLength(1001);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const [url, init] of vi.mocked(fetch).mock.calls) {
      const query = new URL(String(url), "http://localhost").searchParams;
      expect(query.get("weather_only")).toBe("true");
      expect(query.getAll("provider")).toEqual(["python-kma-api"]);
      expect(init?.signal).toBe(controller.signal);
    }
  });
  it("전체 해제는 일반값 옵션과 관계없이 요청하지 않는다", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(getAllPublicLocations([], undefined, true)).resolves.toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("날씨 종류 전달", () => {
  it("위치·최신값·예보·마커에 동일한 종류와 제공처를 전달한다", async () => {
    const fetchMock = respond(JSON.stringify({ data: [], meta: {} }), {
      status: 200, headers: { "content-type": "application/json" },
    });
    const controller = new AbortController();
    const kinds = ["alert", "short"] as const;
    await getPublicLocations(1000, 0, ["python-kma-api"], controller.signal, false, [...kinds]);
    await getLatest("seoul", 1, controller.signal, ["python-kma-api"], [...kinds]);
    await getForecast("seoul", undefined, undefined, 1, controller.signal, ["python-kma-api"], [...kinds]);
    await getMarkerSummaries(["seoul"], controller.signal, ["python-kma-api"], [...kinds]);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    for (const [url, init] of vi.mocked(fetch).mock.calls) {
      const query = new URL(String(url), "http://localhost").searchParams;
      expect(query.getAll("kind")).toEqual(kinds);
      expect(query.getAll("provider")).toEqual(["python-kma-api"]);
      expect(init?.signal).toBe(controller.signal);
    }
  });
  it("종류 전체 해제는 위치 요청을 보내지 않는다", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(getAllPublicLocations(undefined, undefined, false, [])).resolves.toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
