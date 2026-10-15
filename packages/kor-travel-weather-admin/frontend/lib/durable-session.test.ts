import { afterEach, describe, expect, it, vi } from "vitest";
import { checkDurableSession, durableRevokeSession } from "./session";

const env = {
  NODE_ENV: "production",
  WEATHER_API_INTERNAL_URL: "http://weather-api.example.test",
  WEATHER_ADMIN_TOKEN: "test-only-admin-token",
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("durable session store contract", () => {
  it.each([
    [{ revoked: false }, "active"],
    [{ revoked: true }, "revoked"],
    [{}, "unavailable"],
    [[], "unavailable"],
    [null, "unavailable"],
    [{ revoked: "true" }, "unavailable"],
    [{ revoked: 0 }, "unavailable"],
  ])("validates the complete read decision for %j", async (payload, expected) => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(payload));
    vi.stubGlobal("fetch", fetcher);
    await expect(checkDurableSession("test-session", env)).resolves.toBe(expected);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[1]).toMatchObject({ cache: "no-store" });
  });

  it.each([401, 403, 500, 503])("blocks an HTTP %s without revoking a session", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ revoked: false }, { status })));
    await expect(checkDurableSession("test-session", env)).resolves.toBe("unavailable");
  });

  it("blocks an invalid JSON reply", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not-json")));
    await expect(checkDurableSession("test-session", env)).resolves.toBe("unavailable");
  });

  it("allows a slow successful lookup inside the bounded budget", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((resolve, reject) => {
      const timer = setTimeout(() => resolve(Response.json({ revoked: false })), 1500);
      init.signal?.addEventListener("abort", () => { clearTimeout(timer); reject(new Error("aborted")); });
    }));
    vi.stubGlobal("fetch", fetcher);
    const pending = checkDurableSession("test-session", env);
    await vi.advanceTimersByTimeAsync(1500);
    await expect(pending).resolves.toBe("active");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stops an unavailable lookup after five seconds without retrying", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new Error("aborted")));
    }));
    vi.stubGlobal("fetch", fetcher);
    const pending = checkDurableSession("test-session", env);
    await vi.advanceTimersByTimeAsync(4999);
    expect(fetcher.mock.calls[0]?.[1].signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await expect(pending).resolves.toBe("unavailable");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not cache an earlier authorization decision", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ revoked: false }))
      .mockResolvedValueOnce(Response.json({ revoked: true }));
    vi.stubGlobal("fetch", fetcher);
    await expect(checkDurableSession("same-session", env)).resolves.toBe("active");
    await expect(checkDurableSession("same-session", env)).resolves.toBe("revoked");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("blocks incomplete production configuration before network access", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    await expect(checkDurableSession("test-session", { NODE_ENV: "production" })).resolves.toBe("unavailable");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    [{ revoked: true }, true],
    [{ revoked: false }, false],
    [{}, false],
    [[], false],
    [null, false],
    [{ revoked: "true" }, false],
  ])("requires a durable logout acknowledgement for %j", async (payload, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(payload)));
    await expect(durableRevokeSession("test-session", env)).resolves.toBe(expected);
  });
});
