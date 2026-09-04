import { describe, expect, it } from "vitest";

import {
  createSessionValue,
  hashAdminPasswordForEnv,
  revokeSessionValue,
  verifyAdminLogin,
  verifySessionValue,
} from "./session";

const SECRET = "0123456789abcdef0123456789abcdef";
const NOW = 1_800_000_000_000;

describe("admin session contract", () => {
  it("uses the Geo-compatible audience/version and binds a cookie to its user agent", async () => {
    const browser = new Headers({ "user-agent": "weather-admin-test" });
    const otherBrowser = new Headers({ "user-agent": "other-browser" });
    const cookie = await createSessionValue("admin", SECRET, browser, NOW);

    await expect(
      verifySessionValue(cookie, SECRET, browser, "admin", NOW),
    ).resolves.toBe("admin");
    await expect(
      verifySessionValue(cookie, SECRET, otherBrowser, "admin", NOW),
    ).resolves.toBeNull();
    await expect(
      verifySessionValue(cookie, SECRET, browser, "rotated-user", NOW),
    ).resolves.toBeNull();
  });

  it("revokes only the issued session and rejects it before natural expiry", async () => {
    const browser = new Headers({ "user-agent": "weather-admin-test" });
    const cookie = await createSessionValue("admin", SECRET, browser, NOW);

    revokeSessionValue(cookie);

    await expect(
      verifySessionValue(cookie, SECRET, browser, "admin", NOW),
    ).resolves.toBeNull();
  });

  it("accepts a PBKDF2 hash while retaining the explicit compatibility password", async () => {
    const hash = await hashAdminPasswordForEnv(
      "geo-compatible-password",
      new Uint8Array(16).fill(7),
      100_000,
    );
    const hashedEnv = {
      WEATHER_UI_USER: "admin",
      WEATHER_UI_PASSWORD_HASH: hash,
    };

    await expect(
      verifyAdminLogin(
        { username: "admin", password: "geo-compatible-password" },
        hashedEnv,
      ),
    ).resolves.toBe("ok");
    await expect(
      verifyAdminLogin({ username: "admin", password: "wrong" }, hashedEnv),
    ).resolves.toBe("invalid");
  });
});
