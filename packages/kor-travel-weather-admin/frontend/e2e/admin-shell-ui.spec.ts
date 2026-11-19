import { expect, test, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.route("**/api/weather/**", (route) => route.fulfill({ json: { data: [], meta: {} } }));
  await page.goto("/login?next=/locations");
  await expect(page.locator('[data-slot="admin-shell"]')).toHaveCount(0);
  await page.locator('input[name="username"]').fill("admin");
  await page.locator('input[name="password"]').fill(process.env.E2E_UI_PASSWORD!);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page).toHaveURL(/\/locations$/, { timeout: 30_000 });
  await expect(page.locator('[data-slot="admin-shell-header"] h1')).toHaveText("날씨 - 위치 카탈로그");
}

for (const width of [320, 375]) test(`공통 왼쪽 drawer·초점·이동·로그아웃 ${width}px`, async ({ page }) => {
  test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
  await page.setViewportSize({ width, height: 812 });
  await login(page);
  const trigger = page.locator('[data-slot="admin-shell-menu-button"]');
  const drawer = page.getByRole("dialog", { name: "메뉴", exact: true });
  await expect(page.locator('[data-slot="admin-shell-rail"]')).not.toBeVisible();
  await expect(page.locator('[data-slot="admin-shell-header"]')).toHaveCount(1);
  await trigger.click();
  await expect(drawer).toBeVisible();
  // drawer의 열림 transition이 끝난 뒤 최종 왼쪽 위치를 검증한다.
  await expect.poll(async () => Math.abs((await drawer.boundingBox())!.x)).toBeLessThan(0.5);
  const box = await drawer.boundingBox();
  expect(Math.abs(box!.x)).toBeLessThan(0.5);
  expect(box!.width).toBeLessThanOrEqual(width * 0.85);
  await expect(page.getByRole("button", { name: "메뉴 열기", exact: true })).toHaveCount(0);
  await expect(page.locator("body")).toHaveCSS("overflow", "hidden");
  await page.screenshot({ path: `test-results/weather-common-shell-${width}-drawer.png`, fullPage: true });
  await drawer.getByRole("button", { name: "로그아웃", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect.poll(() => drawer.evaluate((el) => el.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await drawer.getByRole("link", { name: "위치 카탈로그", exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(page.locator("#main-content")).toBeFocused();
  await trigger.click();
  await drawer.getByRole("link", { name: "데이터셋", exact: true }).click();
  await expect(page).toHaveURL(/\/datasets$/);
  await expect(drawer).toHaveCount(0);
  await expect(page.locator('[data-slot="admin-shell-header"] h1')).toHaveText("날씨 - 데이터셋");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);

  // 실패 때 세션을 남기고 재시도 성공 뒤에만 로그인으로 이동한다.
  await page.route("**/api/auth/logout", async (route) => {
    await page.unroute("**/api/auth/logout");
    await route.fulfill({ status: 503, json: { detail: "시험 로그아웃 실패" } });
  });
  await page.locator('[data-slot="admin-shell-menu-button"]').click();
  await drawer.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(drawer.getByRole("alert")).toHaveText("로그아웃하지 못했습니다. 다시 시도해 주세요.");
  await expect(page).toHaveURL(/\/datasets$/);
  const cookie = (await page.context().cookies()).map((entry) => `${entry.name}=${entry.value}`).join("; ");
  await drawer.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  const replay = await page.request.get("/api/weather/providers", { headers: { cookie, accept: "application/json" }, maxRedirects: 0 });
  expect(replay.status()).toBe(401);
});

test("PC 세로 레일 접힘은 기존 저장 키에서 복원한다", async ({ page }) => {
  test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
  await page.setViewportSize({ width: 1280, height: 900 });
  await login(page);
  const rail = page.locator('[data-slot="admin-shell-rail"]');
  await expect(rail).toBeVisible();
  await expect(page.locator('[data-slot="admin-shell-menu-button"]')).not.toBeVisible();
  const before = (await rail.boundingBox())!.width;
  await rail.getByRole("button", { name: "메뉴 접기", exact: true }).click();
  await expect(rail).toHaveAttribute("data-collapsed", "true");
  expect((await rail.boundingBox())!.width).toBeLessThan(before);
  await page.reload();
  await expect(rail).toHaveAttribute("data-collapsed", "true");
  await rail.getByRole("button", { name: "메뉴 펼치기", exact: true }).click();
  await expect(rail).not.toHaveAttribute("data-collapsed", "true");
  const links = rail.getByRole("navigation", { name: "주요 메뉴", exact: true }).getByRole("link");
  expect((await links.nth(1).boundingBox())!.y).toBeGreaterThan((await links.nth(0).boundingBox())!.y);
});
