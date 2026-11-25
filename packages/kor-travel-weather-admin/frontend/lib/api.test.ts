import { afterEach, describe, expect, it, vi } from "vitest";

import { forecastWindowEnd, forecastWindowStart, getAllPublicLocations, getPublicLocations, getLatest, getForecast, getMarkerSummaries, getReadiness, type ApiReadiness } from "./api";

function respond(body: string, init: ResponseInit) {
  const fetchMock = vi.fn(async () => new Response(body, init));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.useRealTimers();
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
    vi.useFakeTimers();
    const fetchMock = respond("<html><body><h1>504 Gateway Time-out</h1></body></html>", {
      status: 504,
      headers: { "content-type": "text/html" },
    });
    const failure = expect(getPublicLocations()).rejects.toThrow("요청 실패 (504)");
    // GET 504는 두 번 더 시도한 뒤 마지막 응답의 문장을 보여준다(lib/api-retry.test.ts).
    await vi.runAllTimersAsync();
    await failure;
    expect(fetchMock).toHaveBeenCalledTimes(3);
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

describe("forecastWindowEnd", () => {
  it("is the start of the KST day after tomorrow, in UTC", () => {
    expect(forecastWindowEnd(new Date("2026-11-10T03:37:00Z"))).toBe("2026-11-11T15:00:00.000Z");
    // 15:00Z is already the next KST day: the window moves with the KST date, not UTC.
    expect(forecastWindowEnd(new Date("2026-11-10T15:30:00Z"))).toBe("2026-11-12T15:00:00.000Z");
    expect(forecastWindowEnd(new Date("2026-12-31T14:59:59Z"))).toBe("2027-01-01T15:00:00.000Z");
    // exact KST midnight edge
    expect(forecastWindowEnd(new Date("2026-11-10T14:59:59.999Z"))).toBe("2026-11-11T15:00:00.000Z");
    expect(forecastWindowEnd(new Date("2026-11-10T15:00:00.000Z"))).toBe("2026-11-12T15:00:00.000Z");
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
    const signals: (AbortSignal | null | undefined)[] = [];
    for (const [url, init] of vi.mocked(fetch).mock.calls) {
      const query = new URL(String(url), "http://localhost").searchParams;
      expect(query.get("weather_only")).toBe("true");
      expect(query.getAll("provider")).toEqual(["python-kma-api"]);
      expect(init?.signal?.aborted).toBe(false);
      signals.push(init?.signal);
    }
    // 시도별 20초 상한과 합친 signal이어도 호출자 취소는 그대로 전달된다.
    controller.abort();
    expect(signals.every((signal) => signal?.aborted)).toBe(true);
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
    const signals: (AbortSignal | null | undefined)[] = [];
    for (const [url, init] of vi.mocked(fetch).mock.calls) {
      const query = new URL(String(url), "http://localhost").searchParams;
      expect(query.getAll("kind")).toEqual(kinds);
      expect(query.getAll("provider")).toEqual(["python-kma-api"]);
      signals.push(init?.signal);
    }
    controller.abort();
    expect(signals.every((signal) => signal?.aborted)).toBe(true);
  });
  it("종류 전체 해제는 위치 요청을 보내지 않는다", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(getAllPublicLocations(undefined, undefined, false, [])).resolves.toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("시작 warmup 상태(/ready)", () => {
  const running: ApiReadiness = {
    ready: false, warm: "running", reason: null, elapsed_ms: 120,
    steps: [
      { name: "connections", outcome: "ok", elapsed_ms: 40, warmed: null, planned: null },
      { name: "markers", outcome: "failed", elapsed_ms: 9000, warmed: 45, planned: 120 },
    ],
  };

  it("진행 중 503도 본문을 그대로 돌려준다", async () => {
    const fetchMock = respond(JSON.stringify(running), {
      status: 503, headers: { "content-type": "application/json", "retry-after": "1" },
    });
    await expect(getReadiness()).resolves.toEqual(running);
    expect(fetchMock).toHaveBeenCalledWith("/api/weather/ready", { cache: "no-store" });
  });

  it("완료·생략 200을 돌려준다", async () => {
    const skipped: ApiReadiness = { ...running, ready: true, warm: "skipped", reason: "budget" };
    respond(JSON.stringify(skipped), { status: 200, headers: { "content-type": "application/json" } });
    await expect(getReadiness()).resolves.toEqual(skipped);
  });

  it("그 밖의 상태는 실패로 본다", async () => {
    respond("Bad Gateway", { status: 502, headers: { "content-type": "text/plain" } });
    await expect(getReadiness()).rejects.toThrow("API 연결 실패 (502)");
  });

  it("BFF가 API에 닿지 못한 503 {detail}은 warmup 상태가 아니라 실패다", async () => {
    respond(JSON.stringify({ detail: "날씨 서버 응답을 받지 못했습니다. 잠시 후 다시 시도하세요." }), {
      status: 503, headers: { "content-type": "application/json", "retry-after": "1" },
    });
    await expect(getReadiness()).rejects.toThrow("API 연결 실패 (503)");
  });

  it("JSON이 아닌 503(세션 저장소 장애 등)도 실패다", async () => {
    respond("Service Unavailable", { status: 503, headers: { "content-type": "text/plain" } });
    await expect(getReadiness()).rejects.toThrow("API 연결 실패 (503)");
  });
});
