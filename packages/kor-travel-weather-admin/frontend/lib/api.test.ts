import { afterEach, describe, expect, it, vi } from "vitest";

import { forecastWindowStart, getPublicLocations } from "./api";

function respond(body: string, init: ResponseInit) {
  const fetchMock = vi.fn(async () => new Response(body, init));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request error handling", () => {
  it("reports the status when the middleware answers with a plain-text 401", async () => {
    respond("관리자 UI 인증이 필요합니다.", {
      status: 401,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
    // Parsing before checking the status turned this into
    // `SyntaxError: Unexpected token '관' ...`, hiding the real status.
    await expect(getPublicLocations()).rejects.toThrow("요청 실패 (401)");
  });

  it("reports the status when the backend returns an unhandled 500", async () => {
    respond("Internal Server Error", {
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
