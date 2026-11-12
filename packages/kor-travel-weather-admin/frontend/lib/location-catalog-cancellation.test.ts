import { afterEach, describe, expect, it, vi } from "vitest";
import { getAllPublicLocations } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("전체 카탈로그 취소", () => {
  it("이미 취소된 요청은 첫 페이지도 전송하지 않는다", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(getAllPublicLocations(undefined, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("첫 페이지가 늦게 끝난 뒤에도 취소된 요청의 다음 페이지를 전송하지 않는다", async () => {
    const controller = new AbortController();
    const fetch = vi.fn(async () => {
      controller.abort();
      return new Response(JSON.stringify({ data: Array.from({ length: 1000 }, (_, i) => ({ location_id: String(i) })), meta: {} }), {
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetch);
    await expect(getAllPublicLocations(undefined, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
