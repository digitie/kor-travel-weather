import { expect, test, type Page } from "@playwright/test";

const enabled = process.env.ADDRESS_REFRESH_UI_E2E === "1";
const base = process.env.E2E_BASE_URL ?? "http://localhost:24105";
const local = ["localhost", "127.0.0.1"].includes(new URL(base).hostname);
test.skip(!enabled || !local, "전용 로컬 fixture API·Dagster·UI에서만 명시적으로 실행합니다.");

async function login(page: Page) {
  await page.goto("/login?next=/admin/dagster");
  await page.locator('input[name="username"]').fill(process.env.E2E_UI_USER ?? "admin");
  await page.locator('input[name="password"]').fill(process.env.E2E_UI_PASSWORD ?? "");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.waitForURL("**/admin/dagster");
  await expect(page.getByRole("heading", { name: "주소 갱신", exact: true })).toBeVisible();
}

const fixture = {
  total: 1, due: 1, failed: 1, configured: true, auto_enabled: true,
  in_progress: false, manual_cycle: false, refresh_months: 6,
  last_resolved_at: "2026-01-31T00:00:00+00:00",
  items: [{
    target_key: "fixture", name: "실패 시험 위치", status: "error", stale: true,
    provider: "fixture", address: { full: "마지막 정상 주소" },
    resolved_at: "2026-01-31T00:00:00+00:00", next_refresh_at: "2026-07-31T00:00:00+00:00",
    error_code: "kakao_failed",
  }],
};

for (const viewport of [{ width: 1365, height: 900 }, { width: 390, height: 844 }]) {
  test.describe("주소 갱신 " + viewport.width + "px", () => {
    test.use({ viewport });
    test("실제 로그인·상태 API·파생 주소를 표시하고 화면 전체가 넘치지 않는다", async ({ page }) => {
      await login(page);
      const panel = page.getByRole("region", { name: "주소 갱신", exact: true });
      await expect(panel.getByText("서울특별시 중구 세종대로 110", { exact: true })).toBeVisible();
      await expect(panel.getByRole("button", { name: "전체 주소 수동 갱신" })).toBeEnabled();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
      const results = panel.getByRole("region", { name: "최근 주소 확인 결과", exact: true });
      await results.focus();
      await expect(results).toBeFocused();
      if (viewport.width < 640) {
        await results.press("ArrowRight");
        await expect.poll(() => results.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
      }
      await page.screenshot({ path: "test-results/address-refresh-" + viewport.width + ".png", fullPage: true });
    });
    test("fixture 실패 주소를 최신으로 표시하지 않고 마지막 정상 값을 보존한다", async ({ page }) => {
      await page.route("**/api/address-refresh", route => route.fulfill({ json: fixture }));
      await login(page);
      const panel = page.getByRole("region", { name: "주소 갱신", exact: true });
      await expect(panel.getByText("마지막 정상 주소", { exact: true })).toBeVisible();
      await expect(panel.getByText(/재확인 필요/)).toBeVisible();
      await expect(panel.getByText(/kakao_failed/)).toBeVisible();
    });
    test("fixture 키 미설정과 빈 목록에서는 실행을 차단한다", async ({ page }) => {
      await page.route("**/api/address-refresh", route => route.fulfill({
        json: { ...fixture, total: 0, due: 0, failed: 0, configured: false, items: [] },
      }));
      await login(page);
      await expect(page.getByRole("button", { name: "전체 주소 수동 갱신" })).toBeDisabled();
      await expect(page.getByText("VWorld 또는 Kakao 서버 키를 설정해야 실행할 수 있습니다.")).toBeVisible();
    });
    test("fixture 조회 실패 후 상태 새로고침으로 실제 API에 복구한다", async ({ page }) => {
      await page.route("**/api/address-refresh", route => route.fulfill({ status: 503, json: {} }));
      await login(page);
      await expect(page.getByRole("alert").filter({ hasText: "주소 갱신 상태를 읽지 못했습니다." })).toBeVisible();
      await page.unroute("**/api/address-refresh");
      await page.getByRole("button", { name: "상태 새로고침", exact: true }).click();
      await expect(page.getByText("서울특별시 중구 세종대로 110", { exact: true })).toBeVisible();
    });
  });
}

test("실제 Dagster 예약·같은 요청 재조회·중복 실행 차단·CSRF를 확인한다", async ({ page, browser }) => {
  const unauth = await browser.newContext();
  const denied = await unauth.request.get(base + "/api/address-refresh", { maxRedirects: 0 });
  expect([401, 307]).toContain(denied.status());
  await unauth.close();
  await login(page);
  const badOrigin = await page.request.post("/api/address-refresh", {
    headers: { origin: "https://elsewhere.invalid" },
    data: { requestId: "03147536-8f5c-41ec-a725-be2ca3b024f3" },
  });
  expect(badOrigin.status()).toBe(403);
  const pending = page.waitForResponse(response => response.url().endsWith("/api/address-refresh") && response.request().method() === "POST");
  await page.getByRole("button", { name: "전체 주소 수동 갱신" }).click();
  const result = await pending;
  expect(result.status()).toBe(202);
  const run = await result.json();
  expect(run.status).toBe("QUEUED");
  const requestId = result.request().postDataJSON().requestId;
  await expect(page.getByText(/Daemon이 중지되어 있으면 대기합니다/)).toBeVisible();
  await expect(page.getByRole("link", { name: "예약한 실행 보기 ↗" })).toHaveAttribute("href", new RegExp(run.runId));
  const replay = await page.request.post("/api/address-refresh", { headers: { origin: base }, data: { requestId } });
  expect(replay.status()).toBe(202);
  expect((await replay.json()).runId).toBe(run.runId);
  const duplicate = await page.request.post("/api/address-refresh", {
    headers: { origin: base }, data: { requestId: crypto.randomUUID() },
  });
  expect(duplicate.status()).toBe(409);
});
