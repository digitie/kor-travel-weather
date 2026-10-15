import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { middleware } from "./middleware";
import { POST as logout } from "./app/api/auth/logout/route";
import { createSessionValue, SESSION_COOKIE, verifySessionValue } from "./lib/session";

const secret = "0123456789abcdef0123456789abcdef";
const origin = "https://weather-admin.example.test";
const agent = "weather-session-regression-test";
let cookie: string;

function request(path = "/api/weather/health", method = "GET", includeCookie = true) {
  return new NextRequest(origin + path, {
    method,
    headers: {
      "user-agent": agent,
      origin,
      ...(includeCookie ? { cookie: `${SESSION_COOKIE}=${cookie}` } : {}),
    },
  });
}

beforeEach(async () => {
  for (const [name, value] of Object.entries({
    NODE_ENV: "production", WEATHER_UI_USER: "admin", WEATHER_UI_PASSWORD: "test-only-password",
    WEATHER_UI_SESSION_SECRET: secret, WEATHER_UI_PUBLIC_ORIGIN: origin, WEATHER_API_INTERNAL_URL: "http://weather-api.example.test",
    WEATHER_ADMIN_TOKEN: "test-only-admin-token",
  })) vi.stubEnv(name, value);
  cookie = await createSessionValue("admin", secret, new Headers({ "user-agent": agent }));
});

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("session store failure is distinct from invalid credentials", () => {
  it.each(["/api/weather/health", "/admin/dagster", "/login"])("returns 503 without clearing the valid cookie on %s", async (path) => {
    const fetcher = vi.fn().mockRejectedValue(new Error("store unavailable"));
    vi.stubGlobal("fetch", fetcher);
    const response = await middleware(request(path));
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("retry-after")).toBe("5");
    expect(response.headers.has("set-cookie")).toBe(false);
    expect(response.headers.has("location")).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("blocks malformed store replies and recovers with the same cookie", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({})).mockResolvedValueOnce(Response.json({ revoked: false }));
    vi.stubGlobal("fetch", fetcher);
    expect((await middleware(request())).status).toBe(503);
    expect((await middleware(request())).headers.get("x-middleware-next")).toBe("1");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("still denies an actually revoked cookie", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ revoked: true })));
    expect((await middleware(request())).status).toBe(401);
  });

  it("denies an anonymous request without querying the store", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    expect((await middleware(request("/api/weather/health", "GET", false))).status).toBe(401);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("still blocks cross-origin logout before store access", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const crossOrigin = request("/api/auth/logout", "POST");
    crossOrigin.headers.set("origin", "https://other.example.test");
    expect((await logout(crossOrigin)).status).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("does not revoke locally or clear a cookie after a malformed logout acknowledgement", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({})));
    const response = await logout(request("/api/auth/logout", "POST"));
    expect(response.status).toBe(503);
    expect(response.headers.has("set-cookie")).toBe(false);
    await expect(verifySessionValue(cookie, secret, new Headers({ "user-agent": agent }), "admin")).resolves.toBe("admin");
  });

  it("clears and locally revokes only after a confirmed durable logout", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ revoked: true })));
    const response = await logout(request("/api/auth/logout", "POST"));
    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
    await expect(verifySessionValue(cookie, secret, new Headers({ "user-agent": agent }), "admin")).resolves.toBeNull();
  });
});
