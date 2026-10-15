import { afterEach, describe, expect, it, vi } from "vitest";
import { checkDurableLoginRateLimit, clearDurableLoginFailures, recordDurableLoginFailure } from "./session";

const env = { NODE_ENV: "production", WEATHER_API_INTERNAL_URL: "http://weather-api.example.test", WEATHER_ADMIN_TOKEN: "test-only-token" };
const bucket = "a".repeat(64);
afterEach(() => vi.unstubAllGlobals());

describe.each([checkDurableLoginRateLimit, recordDurableLoginFailure])("login rate limit reply", (lookup) => {
  it.each([
    [{ allowed: true, retry_after: null }, { available: true, retryAfter: null }],
    [{ allowed: false, retry_after: 10 }, { available: true, retryAfter: 10 }],
    [{}, { available: false }],
    [[], { available: false }],
    [null, { available: false }],
    [{ allowed: true }, { available: false }],
    [{ retry_after: null }, { available: false }],
    [{ allowed: "true", retry_after: null }, { available: false }],
    [{ allowed: true, retry_after: 10 }, { available: false }],
    [{ allowed: false, retry_after: null }, { available: false }],
    [{ allowed: false, retry_after: "10" }, { available: false }],
    [{ allowed: false, retry_after: 0 }, { available: false }],
    [{ allowed: false, retry_after: -1 }, { available: false }],
    [{ allowed: false, retry_after: 1.5 }, { available: false }],
    [{ allowed: false, retry_after: 86401 }, { available: false }],
  ])("validates the decision and delay together for %j", async (payload, expected) => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(payload)); vi.stubGlobal("fetch", fetcher);
    await expect(lookup(bucket, env)).resolves.toEqual(expected);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("durable login success acknowledgement", () => {
  it.each([
    [{ cleared: true }, { available: true, retryAfter: null }],
    [{ cleared: false }, { available: false }],
    [{ cleared: "true" }, { available: false }],
    [{}, { available: false }],
    [[], { available: false }],
    [null, { available: false }],
  ])("requires cleared=true for %j", async (payload, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(payload)));
    await expect(clearDurableLoginFailures(bucket, env)).resolves.toEqual(expected);
  });
});
