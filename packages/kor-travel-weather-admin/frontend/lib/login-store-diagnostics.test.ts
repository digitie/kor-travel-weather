import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  checkDurableLoginRateLimit,
  clearDurableLoginFailures,
  recordDurableLoginFailure,
} from "./session";

const env = {
  NODE_ENV: "production",
  WEATHER_API_INTERNAL_URL: "http://synthetic-private-upstream.example.test",
  WEATHER_ADMIN_TOKEN: "synthetic-secret-token",
};
const bucket = "b".repeat(64);
const actions = [
  ["check", checkDurableLoginRateLimit],
  ["failure", recordDurableLoginFailure],
  ["success", clearDurableLoginFailures],
] as const;

beforeEach(() => vi.spyOn(console, "warn").mockImplementation(() => {}));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function diagnostic() {
  const calls = vi.mocked(console.warn).mock.calls;
  expect(calls).toHaveLength(1);
  expect(calls[0]).toHaveLength(1);
  const raw = calls[0][0] as string;
  for (const secret of [env.WEATHER_API_INTERNAL_URL, env.WEATHER_ADMIN_TOKEN, bucket]) {
    expect(raw).not.toContain(secret);
  }
  const value = JSON.parse(raw);
  expect(Object.keys(value).sort()).toEqual(
    ["event", "action", "reason", "upstream_status", "elapsed_ms"].sort(),
  );
  expect(value.event).toBe("weather.auth.rate_limit.unavailable");
  expect(value.elapsed_ms).toBeGreaterThanOrEqual(0);
  expect(Number.isInteger(value.elapsed_ms)).toBe(true);
  return value;
}

describe.each(actions)("durable auth diagnostics: %s", (action, lookup) => {
  it("keeps non-200 fail-closed, emits bounded fields, and never retries", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response("synthetic-sensitive-upstream-body", { status: 502 }),
    );
    vi.stubGlobal("fetch", fetcher);
    await expect(lookup(bucket, env)).resolves.toEqual({ available: false });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(diagnostic()).toMatchObject({ action, reason: "non-200", upstream_status: 502 });
    expect(vi.mocked(console.warn).mock.calls[0][0]).not.toContain("synthetic-sensitive");
  });

  it("does not log transport error messages or nested causes", async () => {
    const fetcher = vi.fn().mockRejectedValue(
      new TypeError("synthetic-user-password", { cause: new Error(env.WEATHER_ADMIN_TOKEN) }),
    );
    vi.stubGlobal("fetch", fetcher);
    await expect(lookup(bucket, env)).resolves.toEqual({ available: false });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(diagnostic()).toMatchObject({ action, reason: "transport", upstream_status: null });
    expect(vi.mocked(console.warn).mock.calls[0][0]).not.toContain("synthetic-user");
  });

  it("separates a malformed 200 reply from transport failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not-json", { status: 200 })));
    await expect(lookup(bucket, env)).resolves.toEqual({ available: false });
    expect(diagnostic()).toMatchObject({ action, reason: "invalid-reply", upstream_status: 200 });
  });

  it("keeps the existing five-second abort and makes only one request", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockImplementation((_input, init: RequestInit) =>
      new Promise((_resolve, reject) => init.signal?.addEventListener(
        "abort", () => reject(new DOMException("aborted", "AbortError")), { once: true },
      )),
    );
    vi.stubGlobal("fetch", fetcher);
    const pending = lookup(bucket, env);
    await vi.advanceTimersByTimeAsync(4999);
    expect(console.warn).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await expect(pending).resolves.toEqual({ available: false });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(diagnostic()).toMatchObject({ action, reason: "timeout", upstream_status: null });
  });
});

it("keeps configuration fail-closed without logging the bucket or configuration", async () => {
  vi.stubGlobal("fetch", vi.fn());
  await expect(checkDurableLoginRateLimit(bucket, { ...env, WEATHER_ADMIN_TOKEN: "" }))
    .resolves.toEqual({ available: false });
  expect(fetch).not.toHaveBeenCalled();
  expect(diagnostic()).toMatchObject({ action: "check", reason: "configuration" });
});

it("a logging failure cannot change the unavailable result", async () => {
  vi.mocked(console.warn).mockImplementation(() => { throw new Error("logger unavailable"); });
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("private error")));
  await expect(checkDurableLoginRateLimit(bucket, env)).resolves.toEqual({ available: false });
});

it("successful acknowledgements stay quiet", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ allowed: true, retry_after: null })));
  await expect(checkDurableLoginRateLimit(bucket, env))
    .resolves.toEqual({ available: true, retryAfter: null });
  expect(console.warn).not.toHaveBeenCalled();
});
