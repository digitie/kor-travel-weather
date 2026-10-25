import { expect, test } from "@playwright/test";

// 저장된 날씨 응답 fixture로 UI만 검증한다. 운영 API·실제 VWorld 타일 수용과 구분한다.
for (const width of [375, 1440]) test.describe(`${width}px 입력 환경`, () => {
  test.use({ hasTouch: width === 375 });
  test(`간결한 날씨 상세·출처 팝업·프로젝트 톤 ${width}px`, async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
  await page.setViewportSize({ width, height: width === 375 ? 812 : 900 });
  const stamp = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
  const location = { location_id: "compact-map-fixture", name: "날씨 상세 시험 위치", latitude: 36.2, longitude: 127.8, nx: null, ny: null, region_code: null, enabled: true, metadata: {} };
  const sourceKey = "fixture-source-record-key-with-full-lineage-20261025";
  const value = { value_id: "temperature", location_id: location.location_id, provider: "python-kma-api", dataset_key: "nowcast", weather_domain: "weather", forecast_style: "nowcast", metric_key: "T1H", metric_name: "기온", value_number: 20, value_text: null, unit: "°C", target_at: stamp, collected_at: stamp, source_record_key: sourceKey };
  const readings = [value, ...Array.from({ length: 31 }, (_, index) => ({ ...value, value_id: `extra-${index}`, metric_key: `fixture-${index}`, metric_name: `추가 시험값 ${index}`, source_record_key: `${sourceKey}-${index}` }))];
  await page.route("**/api/weather/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const data = pathname.endsWith("/providers") ? [{ provider: "python-kma-api", label: "기상청", datasets: [{ key: "nowcast", label: "초단기실황" }] }]
      : pathname.endsWith("/markers") ? [{ location_id: location.location_id, latest: [value], forecast: [], alerts: [{ ...value, value_id: "alert", value_text: "호우주의보", metric_name: "기상특보", severity: "주의" }] }]
      : pathname.endsWith("/latest") ? readings
      : pathname.endsWith("/locations") ? [location] : [];
    return route.fulfill({ json: { data, meta: {} } });
  });
  await page.goto("/login?next=/weather");
  await page.locator('input[name="username"]').fill("admin");
  await page.locator('input[name="password"]').fill(process.env.E2E_UI_PASSWORD!);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page).toHaveURL(/\/weather$/, { timeout: 30_000 });
  const pageInfo = page.getByRole("button", { name: "날씨 지도 안내", exact: true });
  await expect(pageInfo).toBeVisible();
  expect(await page.evaluate(() => matchMedia("(pointer: coarse)").matches)).toBe(width === 375);
  await expect(pageInfo).toHaveCSS("width", width === 375 ? "44px" : "28px");
  await expect(pageInfo.locator("svg")).toHaveAttribute("width", "15");
  await expect(page.locator(".page-header")).toHaveCSS("border-top-width", "0px");
  await expect(page.locator(".page-header")).toHaveCSS("padding-top", "0px");
  await expect(page.locator(".page-header .description")).toHaveCount(0);
  await pageInfo.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "날씨 지도", exact: true })).toContainText("종류와 데이터 출처");
  await page.keyboard.press("Escape");
  await expect(pageInfo).toBeFocused();

  await page.getByRole("tab", { name: "목록", exact: true }).click();
  await page.locator(".location-list").getByRole("button", { name: /날씨 상세 시험 위치/ }).click();
  const detail = page.getByRole("complementary", { name: "선택한 위치 상세" });
  await expect(detail).toBeVisible();
  await expect(detail.locator(".metric-card.primary").getByText("20 °C", { exact: true })).toBeVisible();
  await expect(detail.getByText("오래됨", { exact: true })).toBeVisible();
  await expect(detail.locator(".inspector-freshness")).toContainText("수집");
  await expect(detail.getByText("기상특보", { exact: true })).toBeVisible();
  await expect(detail.getByText("호우주의보 · 주의", { exact: true })).toBeVisible();
  await expect(detail.locator(".inspector-heading")).not.toContainText(location.location_id);
  await expect(page.getByText(sourceKey, { exact: true })).toHaveCount(0);

  for (const theme of ["light", "dark"]) {
    await page.evaluate((dark) => document.documentElement.classList.toggle("dark", dark), theme === "dark");
    const info = detail.getByRole("button", { name: "위치·데이터 출처 안내", exact: true });
    await info.focus();
    await page.keyboard.press("Enter");
    const popup = page.getByRole("dialog", { name: "위치·데이터 출처", exact: true });
    await expect(popup).toBeVisible();
    await expect(popup).toContainText(location.location_id);
    await expect(popup).toContainText("36.2000, 127.8000");
    await expect(popup).toContainText("기상청 · 초단기실황");
    await popup.getByText("원본 레코드 32개", { exact: true }).click();
    await expect(popup.getByText(sourceKey, { exact: true })).toBeVisible();
    const popupBox = await popup.boundingBox();
    expect(popupBox!.x).toBeGreaterThanOrEqual(0);
    expect(popupBox!.x + popupBox!.width).toBeLessThanOrEqual(width);
    const tone = await page.evaluate(() => {
      const probe = document.createElement("span");
      document.body.append(probe);
      const color = (variable: string) => { probe.style.color = `var(${variable})`; return getComputedStyle(probe).color; };
      const names = ["surface-card", "surface-subtle", "text-primary", "text-secondary", "brand", "brand-tint", "focus", "warning", "destructive"];
      const local = ["card", "surface-subtle", "text-primary", "text-secondary", "brand", "brand-tint", "focus", "warning", "destructive"];
      const matches = names.map((name, index) => color(`--kt-${name}`) === color(`--${local[index]}`));
      const popupBackground = getComputedStyle(document.querySelector(".info-popup")!).backgroundColor;
      const expectedPopup = color("--popover");
      const popupOverflow = getComputedStyle(document.querySelector(".info-popup")!).overflowY;
      probe.remove();
      return { matches, popupBackground, expectedPopup, popupOverflow };
    });
    expect(tone.matches.every(Boolean)).toBe(true);
    expect(tone.popupBackground).toBe(tone.expectedPopup);
    expect(tone.popupOverflow).toBe("auto");
    const scrollBefore = await page.evaluate(() => window.scrollY);
    expect(await popup.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
    await popup.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    expect(await page.evaluate(() => window.scrollY)).toBe(scrollBefore);
    await popup.evaluate((element) => { element.scrollTop = 0; });
    await testInfo.attach(`지도와 상세 치수 ${width}px ${theme}`, { body: JSON.stringify({ stage: await page.locator(".kt-map-stage").boundingBox(), detail: await detail.boundingBox(), popup: popupBox, tone }), contentType: "application/json" });
    await page.screenshot({ path: `test-results/weather-compact-${testInfo.project.name}-${width}-${theme}-info.png`, fullPage: true });
    await page.keyboard.press("Escape");
    await expect(popup).toHaveCount(0);
    await expect(detail).toBeVisible();
    await expect(info).toBeFocused();
    await page.screenshot({ path: `test-results/weather-compact-${testInfo.project.name}-${width}-${theme}.png`, fullPage: true });
  }
  await page.route("**/api/weather/**/latest?**", (route) => route.fulfill({ json: { data: [], meta: {} } }));
  await detail.getByRole("button", { name: "날씨 새로고침", exact: true }).click();
  await expect(detail.getByText("아직 수집된 최신 값이 없습니다.", { exact: true })).toBeVisible();
  await expect(detail.locator(".inspector-freshness")).toContainText("수집 시각 없음");
  await page.unroute("**/api/weather/**/latest?**");
  await page.route("**/api/weather/**/latest?**", (route) => route.fulfill({ status: 503, json: { detail: "시험 자료 조회 실패" } }));
  await detail.getByRole("button", { name: "날씨 새로고침", exact: true }).click();
  await expect(detail.getByText("시험 자료 조회 실패", { exact: true })).toBeVisible();
  await detail.focus();
  await page.keyboard.press("Escape");
  await expect(detail).toHaveCount(0);
  await expect(page.getByRole("searchbox", { name: "위치 검색" })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
});
