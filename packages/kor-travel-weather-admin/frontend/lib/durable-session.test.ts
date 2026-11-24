import { afterEach, describe, expect, it, vi } from "vitest";
import {
  checkDurableSession,
  clearDurableSessionCache,
  durableRevokeSession,
  durableSessionCacheAllowed,
  durableSessionCacheTtlMs,
  forgetDurableSession,
  revokeSessionValue,
} from "./session";

const env = {
  NODE_ENV: "production",
  WEATHER_API_INTERNAL_URL: "http://weather-api.example.test",
  WEATHER_ADMIN_TOKEN: "test-only-admin-token",
};
const uncachedEnv = { ...env, WEATHER_UI_SESSION_CHECK_CACHE_SECONDS: "0" };

afterEach(() => {
  clearDurableSessionCache();
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

  it("does not cache an earlier authorization decision when the cache is disabled", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ revoked: false }))
      .mockResolvedValueOnce(Response.json({ revoked: true }));
    vi.stubGlobal("fetch", fetcher);
    await expect(checkDurableSession("same-session", uncachedEnv)).resolves.toBe("active");
    await expect(checkDurableSession("same-session", uncachedEnv)).resolves.toBe("revoked");
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

describe("durable 세션 확인 캐시", () => {
  const active = () => Response.json({ revoked: false });
  const revoked = () => Response.json({ revoked: true });

  it.each([
    [undefined, 10_000], ["", 10_000], ["0", 0], ["5", 5_000], ["2.5", 2_500],
    ["600", 60_000], ["abc", 0], ["-1", 0],
  ])("WEATHER_UI_SESSION_CHECK_CACHE_SECONDS=%j는 %ims로 해석한다", (value, expected) => {
    expect(durableSessionCacheTtlMs({ ...env, WEATHER_UI_SESSION_CHECK_CACHE_SECONDS: value })).toBe(expected);
  });

  it("active 판정은 TTL 동안만 재사용하고 만료 뒤 다시 확인한다", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockImplementation(async () => active());
    vi.stubGlobal("fetch", fetcher);
    await expect(checkDurableSession("cached-session", env)).resolves.toBe("active");
    await vi.advanceTimersByTimeAsync(9_999);
    await expect(checkDurableSession("cached-session", env)).resolves.toBe("active");
    expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await expect(checkDurableSession("cached-session", env)).resolves.toBe("active");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("설정한 TTL을 따른다", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockImplementation(async () => active());
    vi.stubGlobal("fetch", fetcher);
    const shortEnv = { ...env, WEATHER_UI_SESSION_CHECK_CACHE_SECONDS: "2" };
    await checkDurableSession("short-session", shortEnv);
    await vi.advanceTimersByTimeAsync(1_999);
    await checkDurableSession("short-session", shortEnv);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await checkDurableSession("short-session", shortEnv);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("revoked 판정은 저장하지 않는다", async () => {
    const fetcher = vi.fn().mockImplementationOnce(async () => revoked())
      .mockImplementationOnce(async () => revoked());
    vi.stubGlobal("fetch", fetcher);
    await expect(checkDurableSession("revoked-session", env)).resolves.toBe("revoked");
    await expect(checkDurableSession("revoked-session", env)).resolves.toBe("revoked");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("저장소 장애는 저장하지 않고 다음 요청에서 다시 확인한다", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("store unavailable"))
      .mockResolvedValueOnce(Response.json({ revoked: false }, { status: 503 }))
      .mockImplementation(async () => active());
    vi.stubGlobal("fetch", fetcher);
    await expect(checkDurableSession("flaky-session", env)).resolves.toBe("unavailable");
    await expect(checkDurableSession("flaky-session", env)).resolves.toBe("unavailable");
    await expect(checkDurableSession("flaky-session", env)).resolves.toBe("active");
    await expect(checkDurableSession("flaky-session", env)).resolves.toBe("active");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("같은 쿠키의 동시 확인은 요청 하나를 공유한다", async () => {
    let release!: (response: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const pending = Array.from({ length: 5 }, () => checkDurableSession("burst-session", env));
    expect(fetcher).toHaveBeenCalledTimes(1);
    release(active());
    await expect(Promise.all(pending)).resolves.toEqual(Array(5).fill("active"));
    await expect(checkDurableSession("burst-session", env)).resolves.toBe("active");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("공유한 확인이 실패하면 모두 fail-closed이고 다음 요청은 새로 확인한다", async () => {
    let release!: (response: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }))
      .mockImplementation(async () => active());
    vi.stubGlobal("fetch", fetcher);
    const pending = [checkDurableSession("burst-session", env), checkDurableSession("burst-session", env)];
    release(Response.json({}, { status: 500 }));
    await expect(Promise.all(pending)).resolves.toEqual(["unavailable", "unavailable"]);
    await expect(checkDurableSession("burst-session", env)).resolves.toBe("active");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("다른 쿠키는 서로의 판정을 공유하지 않는다", async () => {
    const fetcher = vi.fn().mockImplementationOnce(async () => active())
      .mockImplementationOnce(async () => revoked());
    vi.stubGlobal("fetch", fetcher);
    await expect(checkDurableSession("first-session", env)).resolves.toBe("active");
    await expect(checkDurableSession("second-session", env)).resolves.toBe("revoked");
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({ session: "second-session" });
  });

  it("forget은 캐시된 active를 지워 폐기를 바로 반영한다", async () => {
    const fetcher = vi.fn().mockImplementationOnce(async () => active())
      .mockImplementationOnce(async () => revoked());
    vi.stubGlobal("fetch", fetcher);
    await expect(checkDurableSession("logout-session", env)).resolves.toBe("active");
    forgetDurableSession("logout-session");
    await expect(checkDurableSession("logout-session", env)).resolves.toBe("revoked");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("forget 전에 시작한 확인의 active 결과는 저장하지 않는다", async () => {
    let release!: (response: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }))
      .mockImplementation(async () => revoked());
    vi.stubGlobal("fetch", fetcher);
    const before = checkDurableSession("racing-session", env);
    forgetDurableSession("racing-session");
    // forget 뒤 요청은 이전 확인을 공유하지 않고 새로 확인한다.
    const after = checkDurableSession("racing-session", env);
    release(active());
    await expect(before).resolves.toBe("active");
    await expect(after).resolves.toBe("revoked");
    await expect(checkDurableSession("racing-session", env)).resolves.toBe("revoked");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("forget 뒤 60초 동안은 active도 저장하지 않는다", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockImplementation(async () => active());
    vi.stubGlobal("fetch", fetcher);
    forgetDurableSession("pending-logout");
    await checkDurableSession("pending-logout", env);
    await checkDurableSession("pending-logout", env);
    expect(fetcher).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(60_000);
    await checkDurableSession("pending-logout", env);
    await checkDurableSession("pending-logout", env);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("로컬 폐기(revokeSessionValue)도 캐시된 판정을 지운다", async () => {
    const fetcher = vi.fn().mockImplementationOnce(async () => active())
      .mockImplementationOnce(async () => revoked());
    vi.stubGlobal("fetch", fetcher);
    await checkDurableSession("local-revoke", env);
    revokeSessionValue("local-revoke");
    await expect(checkDurableSession("local-revoke", env)).resolves.toBe("revoked");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("항목 수 상한을 넘으면 가장 오래된 판정부터 버린다", async () => {
    const fetcher = vi.fn().mockImplementation(async () => active());
    vi.stubGlobal("fetch", fetcher);
    for (let index = 0; index <= 1024; index += 1) await checkDurableSession(`bounded-${index}`, env);
    expect(fetcher).toHaveBeenCalledTimes(1025);
    await checkDurableSession("bounded-1024", env);
    expect(fetcher).toHaveBeenCalledTimes(1025);
    await checkDurableSession("bounded-0", env);
    expect(fetcher).toHaveBeenCalledTimes(1026);
  });

  it.each([
    ["GET", true], ["get", true], ["HEAD", true],
    ["POST", false], ["PATCH", false], ["PUT", false], ["DELETE", false], ["OPTIONS", false],
  ])("%s의 캐시 사용 허용은 %s다", (method, expected) => {
    expect(durableSessionCacheAllowed(method)).toBe(expected);
  });

  it("fresh 확인은 캐시된 active를 쓰지 않고 저장소에 다시 묻는다", async () => {
    const fetcher = vi.fn().mockImplementationOnce(async () => active())
      .mockImplementationOnce(async () => revoked());
    vi.stubGlobal("fetch", fetcher);
    await expect(checkDurableSession("write-session", env)).resolves.toBe("active");
    await expect(checkDurableSession("write-session", env, { fresh: true })).resolves.toBe("revoked");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("fresh 확인이 revoked면 읽기 캐시도 버려 이후 GET이 다시 확인한다", async () => {
    const fetcher = vi.fn().mockImplementationOnce(async () => active())
      .mockImplementation(async () => revoked());
    vi.stubGlobal("fetch", fetcher);
    await checkDurableSession("write-session", env);
    await checkDurableSession("write-session", env, { fresh: true });
    await expect(checkDurableSession("write-session", env)).resolves.toBe("revoked");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("fresh 확인은 진행 중인 읽기 확인을 공유하지 않고 그 결과를 캐시에 넣지 않는다", async () => {
    let release!: (response: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }))
      .mockImplementation(async () => revoked());
    vi.stubGlobal("fetch", fetcher);
    const read = checkDurableSession("racing-write", env);
    await expect(checkDurableSession("racing-write", env, { fresh: true })).resolves.toBe("revoked");
    release(active());
    await expect(read).resolves.toBe("active");
    await expect(checkDurableSession("racing-write", env)).resolves.toBe("revoked");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("fresh 확인의 active는 저장하지 않는다", async () => {
    const fetcher = vi.fn().mockImplementation(async () => active());
    vi.stubGlobal("fetch", fetcher);
    await checkDurableSession("write-only", env, { fresh: true });
    await checkDurableSession("write-only", env);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("0이면 동시 확인도 공유하지 않는다(이전 동작)", async () => {
    const fetcher = vi.fn().mockImplementation(async () => active());
    vi.stubGlobal("fetch", fetcher);
    await Promise.all([checkDurableSession("plain", uncachedEnv), checkDurableSession("plain", uncachedEnv)]);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
