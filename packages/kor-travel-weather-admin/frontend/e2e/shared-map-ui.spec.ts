import { expect, test } from "@playwright/test";

for (const width of [375, 1440]) test(`공통 날씨 종류·필터·오버레이 ${width}px`, async ({ page }) => {
  test.setTimeout(90_000);
  test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
  await page.setViewportSize({ width, height: 1000 });
  const stamp = new Date().toISOString();
  const location = { location_id: "map-kind-fixture", name: "종류 시험 위치", latitude: 36.2, longitude: 127.8, nx: null, ny: null, region_code: null, enabled: true, metadata: {} };
  const forecast = { value_id: "mid-sky", location_id: location.location_id, provider: "python-kma-api", dataset_key: "mid", weather_domain: "weather", forecast_style: "mid", metric_key: "SKY", value_number: null, value_text: "맑음", target_at: stamp, collected_at: stamp, source_record_key: "fixture" };
  const queries: URLSearchParams[] = [];
  await page.route("**/api/weather/**", (route) => {
    const url = new URL(route.request().url());
    queries.push(url.searchParams);
    const data = url.pathname.endsWith("/providers") ? [{ provider: "python-kma-api", label: "기상청", datasets: [{ key: "mid", label: "중기예보" }] }]
      : url.pathname.endsWith("/markers") ? [{ location_id: location.location_id, latest: [], forecast: [forecast], alerts: [] }]
      : url.pathname.endsWith("/forecast") ? [forecast]
      : url.pathname.endsWith("/locations") ? [location] : [];
    return route.fulfill({ json: { data, meta: {} } });
  });
  await page.goto("/login?next=/weather");
  await page.locator('input[name="username"]').fill("admin");
  await page.locator('input[name="password"]').fill(process.env.E2E_UI_PASSWORD!);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page).toHaveURL(/\/weather$/, { timeout: 30_000 });
  const kinds = page.getByRole("group", { name: "종류 필터" });
  for (const label of ["특보", "실황", "초단기", "단기", "중기", "관측"]) await expect(kinds.getByRole("button", { name: label, exact: true })).toBeVisible({ timeout: 30_000 });
  const sizes = await page.locator("[data-slot=map-filter-bar]").evaluate((element) => [
    element.querySelector('input[type="search"]')!, element.querySelector(".kt-map-select-trigger")!, element.querySelector(".kt-map-kinds > button")!,
  ].map((node) => ({ height: node.getBoundingClientRect().height, top: node.getBoundingClientRect().top, width: node.getBoundingClientRect().width })));
  expect(sizes.map((item) => item.height)).toEqual([30, 30, 30]);
  if (width === 1440) {
    expect(sizes[0].width).toBe(176);
    expect(sizes[1].width).toBe(176);
    expect(Math.max(...sizes.map((item) => item.top)) - Math.min(...sizes.map((item) => item.top))).toBeLessThan(2);
  }
  for (const label of ["특보", "실황", "초단기", "단기", "관측"]) await kinds.getByRole("button", { name: label, exact: true }).click();
  await expect.poll(() => queries.some((query) => JSON.stringify(query.getAll("kind")) === '["mid"]')).toBe(true);
  await expect(page.locator(".kt-map-coordinates")).toHaveText(/center -?\d+\.\d{4}, -?\d+\.\d{4} · z \d+\.\d/, { timeout: 30_000 });
  await page.getByRole("tab", { name: "목록", exact: true }).click();
  await page.locator(".location-list").getByRole("button", { name: /종류 시험 위치/ }).click();
  const detail = page.getByRole("complementary", { name: "선택한 위치 상세" });
  await expect(detail).toBeVisible();
  await expect(detail).toHaveCSS("position", "absolute");
  await expect(page.locator(".kt-map-coordinates")).toContainText("center 127.8000, 36.2000");
  const stage = await page.locator(".kt-map-stage").boundingBox();
  const overlay = await detail.boundingBox();
  expect(overlay!.x).toBeGreaterThanOrEqual(stage!.x);
  expect(overlay!.x + overlay!.width).toBeLessThanOrEqual(stage!.x + stage!.width + 1);
  await page.screenshot({ path: `test-results/weather-shared-overlay-${width}.png`, fullPage: true });
  await detail.focus();
  await page.keyboard.press("Escape");
  await expect(detail).toHaveCount(0);
  await expect(page.getByRole("searchbox", { name: "위치 검색" })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/weather-shared-ui-${width}.png`, fullPage: true });
});
