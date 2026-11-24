import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createLocation,
  deleteProviderCredential,
  GET_ATTEMPT_TIMEOUT_MS,
  getMarkerSummaries,
  getPublicLocations,
  retryDelayMs,
  updateLocation,
  updateProviderCredential,
} from "./api";

const envelope = { data: [{ location_id: "seoul" }], meta: { request_id: "r", generated_at: "t", duration_ms: 1 } };
const ok = () => Response.json(envelope);
const status = (code: number, headers: Record<string, string> = {}) =>
  Response.json({ detail: `원천 ${code}` }, { status: code, headers });
const networkFailure = () => new TypeError("Failed to fetch");

// Node의 AbortSignal.timeout은 가짜 timer를 따르지 않으므로 같은 의미의 signal로 바꾼다.
function fakeAttemptTimeouts() {
  return vi.spyOn(AbortSignal, "timeout").mockImplementation((ms: number) => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("signal timed out", "TimeoutError")), ms);
    return controller.signal;
  });
}

function hangUntilAborted() {
  return (_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("retryDelayMs", () => {
  it("지수 backoff에 절반까지 jitter를 더한다", () => {
    expect(retryDelayMs(0, null, () => 0)).toBe(500);
    expect(retryDelayMs(0, null, () => 0.999)).toBe(750);
    expect(retryDelayMs(1, null, () => 0)).toBe(1000);
    expect(retryDelayMs(1, null, () => 0.999)).toBe(1500);
  });

  it("Retry-After 초·HTTP 날짜를 최소 대기로 존중한다", () => {
    expect(retryDelayMs(0, "1", () => 0)).toBe(1000);
    expect(retryDelayMs(0, "5", () => 0.5)).toBe(5125);
    const now = Date.parse("2026-11-24T00:00:00Z");
    expect(retryDelayMs(0, "Tue, 24 Nov 2026 00:00:03 GMT", () => 0, now)).toBe(3000);
    expect(retryDelayMs(1, "Tue, 24 Nov 2026 00:00:00 GMT", () => 0, now)).toBe(1000);
  });

  it("해석할 수 없는 Retry-After는 무시하고 10초를 넘으면 재시도하지 않는다", () => {
    expect(retryDelayMs(0, "soon", () => 0)).toBe(500);
    expect(retryDelayMs(0, "-1", () => 0)).toBe(500);
    expect(retryDelayMs(0, "10", () => 0)).toBe(10_000);
    expect(retryDelayMs(0, "11", () => 0)).toBeNull();
  });
});

describe("GET 재시도", () => {
  it.each([502, 503, 504])("HTTP %s 뒤 backoff를 기다려 다시 보내고 성공을 돌려준다", async (code) => {
    const fetcher = vi.fn().mockResolvedValueOnce(status(code)).mockResolvedValueOnce(ok());
    vi.stubGlobal("fetch", fetcher);
    let settled = false;
    const pending = getPublicLocations().finally(() => { settled = true; });
    await vi.advanceTimersByTimeAsync(499);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await expect(pending).resolves.toMatchObject({ data: [{ location_id: "seoul" }] });
    expect(settled).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("Retry-After보다 먼저 다시 보내지 않는다", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(status(503, { "retry-after": "5" }))
      .mockResolvedValueOnce(ok());
    vi.stubGlobal("fetch", fetcher);
    const pending = getMarkerSummaries(["seoul"]);
    await vi.advanceTimersByTimeAsync(4_999);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await expect(pending).resolves.toMatchObject({ data: [{ location_id: "seoul" }] });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("최대 두 번 다시 보낸 뒤 마지막 응답의 문장으로 실패한다", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(status(503)).mockResolvedValueOnce(status(502))
      .mockResolvedValueOnce(status(504));
    vi.stubGlobal("fetch", fetcher);
    const failure = expect(getPublicLocations()).rejects.toThrow("원천 504");
    await vi.runAllTimersAsync();
    await failure;
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("Retry-After가 10초를 넘으면 기다리지 않고 바로 알린다", async () => {
    const fetcher = vi.fn().mockResolvedValue(status(503, { "retry-after": "60" }));
    vi.stubGlobal("fetch", fetcher);
    await expect(getPublicLocations()).rejects.toThrow("원천 503");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each([400, 401, 403, 404, 409, 429, 500])("HTTP %s는 다시 보내지 않는다", async (code) => {
    const fetcher = vi.fn().mockResolvedValue(status(code));
    vi.stubGlobal("fetch", fetcher);
    await expect(getPublicLocations()).rejects.toThrow(`원천 ${code}`);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("네트워크 오류는 두 번까지 다시 보낸다", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(networkFailure()).mockRejectedValueOnce(networkFailure())
      .mockResolvedValueOnce(ok());
    vi.stubGlobal("fetch", fetcher);
    const pending = getPublicLocations();
    await vi.runAllTimersAsync();
    await expect(pending).resolves.toMatchObject({ data: [{ location_id: "seoul" }] });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("네트워크 오류가 계속되면 원래 오류로 끝난다", async () => {
    const fetcher = vi.fn().mockRejectedValue(networkFailure());
    vi.stubGlobal("fetch", fetcher);
    const failure = expect(getPublicLocations()).rejects.toThrow("Failed to fetch");
    await vi.runAllTimersAsync();
    await failure;
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("TypeError가 아닌 예외는 코드 결함이므로 다시 보내지 않는다", async () => {
    const fetcher = vi.fn().mockRejectedValue(new RangeError("defect"));
    vi.stubGlobal("fetch", fetcher);
    await expect(getPublicLocations()).rejects.toThrow("defect");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("GET 시도별 20초 상한", () => {
  it("응답이 없으면 20초마다 끊고 다시 보낸 뒤 시간 초과 문장으로 실패한다", async () => {
    fakeAttemptTimeouts();
    const fetcher = vi.fn().mockImplementation(hangUntilAborted());
    vi.stubGlobal("fetch", fetcher);
    let failure: unknown;
    const pending = getPublicLocations().catch((error: unknown) => { failure = error; });
    await vi.advanceTimersByTimeAsync(GET_ATTEMPT_TIMEOUT_MS - 1);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetcher.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    await vi.runAllTimersAsync();
    await pending;
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(failure).toBeInstanceOf(Error);
    expect((failure as Error).message).toBe("날씨 서버 응답이 20초 안에 오지 않았습니다. 잠시 후 다시 시도하세요.");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("본문 전송이 멈춰도 같은 상한으로 끊고 다시 보낸다", async () => {
    fakeAttemptTimeouts();
    const stalled = (_url: string, init?: RequestInit) => Promise.resolve(new Response(new ReadableStream({
      start(controller) {
        init?.signal?.addEventListener("abort", () => controller.error(init.signal?.reason), { once: true });
      },
    }), { status: 200 }));
    const fetcher = vi.fn().mockImplementationOnce(stalled).mockResolvedValueOnce(ok());
    vi.stubGlobal("fetch", fetcher);
    const pending = getPublicLocations();
    await vi.advanceTimersByTimeAsync(GET_ATTEMPT_TIMEOUT_MS);
    await vi.runAllTimersAsync();
    await expect(pending).resolves.toMatchObject({ data: [{ location_id: "seoul" }] });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe("호출자 취소", () => {
  it("대기 중 취소는 즉시 AbortError로 끝나고 더 보내지 않는다", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn().mockResolvedValue(status(503, { "retry-after": "5" }));
    vi.stubGlobal("fetch", fetcher);
    const pending = getMarkerSummaries(["seoul"], controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(1_000);
    controller.abort();
    await rejected;
    await vi.runAllTimersAsync();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("전송 중 취소는 재시도로 바꾸지 않는다", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn().mockImplementation(hangUntilAborted());
    vi.stubGlobal("fetch", fetcher);
    const pending = getMarkerSummaries(["seoul"], controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await rejected;
    await vi.runAllTimersAsync();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("비멱등 요청", () => {
  const writes = [
    ["POST", () => createLocation({ location_id: "a", name: "a", latitude: 37, longitude: 127, nx: null, ny: null })],
    ["PATCH", () => updateLocation("a", { enabled: false })],
    ["PUT", () => updateProviderCredential("python-kma-api", "test-only-key")],
    ["DELETE", () => deleteProviderCredential("python-kma-api")],
  ] as const;

  it.each(writes)("%s의 503은 다시 보내지 않는다", async (method, call) => {
    const fetcher = vi.fn().mockResolvedValue(status(503, { "retry-after": "1" }));
    vi.stubGlobal("fetch", fetcher);
    await expect(call()).rejects.toThrow("원천 503");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[1]?.method).toBe(method);
  });

  it.each(writes)("%s의 네트워크 오류도 다시 보내지 않고 새 시간 상한을 두지 않는다", async (_method, call) => {
    const timeout = fakeAttemptTimeouts();
    const fetcher = vi.fn().mockRejectedValue(networkFailure());
    vi.stubGlobal("fetch", fetcher);
    await expect(call()).rejects.toThrow("Failed to fetch");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[1]?.signal ?? null).toBeNull();
    expect(timeout).not.toHaveBeenCalled();
  });
});
