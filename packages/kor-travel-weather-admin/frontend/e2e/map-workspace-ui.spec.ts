import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// UI 작업면 fixture다. 실제 기상 제공처·운영 타일 수용과 구분한다.
async function openWorkspace(page: Page, options: { delayedCatalog?: boolean; emptyCatalog?: boolean; failedCatalog?: boolean; failedProviders?: boolean } = {}) {
  const stamp = new Date().toISOString();
  const locations = Array.from({ length: 80 }, (_, index) => ({ location_id: `workspace-${index}`, name: index === 0 ? "지도 작업면 시험 위치" : `시험 위치 ${index}`, latitude: 36.2, longitude: 127.8, nx: null, ny: null, region_code: null, enabled: true, metadata: {} }));
  const value = { value_id: "temperature", location_id: "workspace-0", provider: "python-kma-api", dataset_key: "nowcast", weather_domain: "weather", forecast_style: "nowcast", metric_key: "T1H", metric_name: "기온", value_number: 20, value_text: null, unit: "°C", target_at: stamp, collected_at: stamp, source_record_key: "workspace-fixture-source-record-key-with-full-lineage" };
  const queries: URL[] = [];
  let releaseCatalog: (() => Promise<void>) | undefined;
  await page.route("**/api/weather/**", async (route) => {
    const url = new URL(route.request().url());
    queries.push(url);
    if (url.pathname.endsWith("/providers") && options.failedProviders) {
      await route.fulfill({ status: 503, json: { detail: "시험 출처 목록 조회 실패" } });
      return;
    }
    if (url.pathname.endsWith("/locations") && options.delayedCatalog) {
      await new Promise<void>((resolve) => { releaseCatalog = async () => { await route.fulfill({ json: { data: locations, meta: {} } }); resolve(); }; });
      return;
    }
    if (url.pathname.endsWith("/locations") && options.failedCatalog) {
      await route.fulfill({ status: 503, json: { detail: "시험 위치 카탈로그 조회 실패" } });
      return;
    }
    const data = url.pathname.endsWith("/providers") ? [{ provider: "python-kma-api", label: "기상청", datasets: [{ key: "nowcast", label: "초단기실황" }] }]
      : url.pathname.endsWith("/locations") ? options.emptyCatalog ? [] : locations
      : url.pathname.endsWith("/markers") ? url.searchParams.getAll("location_id").map((id) => ({ location_id: id, latest: [{ ...value, location_id: id }], forecast: [], alerts: [{ ...value, value_id: `${id}-alert`, value_text: "호우주의보", severity: "주의" }], measurement_point: null }))
      : url.pathname.endsWith("/latest") ? [{ ...value, location_id: url.pathname.split("/").at(-2) }]
      : [];
    await route.fulfill({ json: { data, meta: {} } });
  });
  await page.goto("/login?next=/weather");
  await page.locator('input[name="username"]').fill("admin");
  await page.locator('input[name="password"]').fill(process.env.E2E_UI_PASSWORD!);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page).toHaveURL(/\/weather$/, { timeout: 30_000 });
  await expect(page.locator('[data-slot="map-stage"]:visible')).toBeVisible();
  await expect(page.locator(".kt-map-coordinates")).toContainText("center");
  if (!options.delayedCatalog) await expect(page.getByText("위치 카탈로그를 불러오는 중…", { exact: true })).toHaveCount(0);
  return { queries, releaseCatalog: async () => { await expect.poll(() => Boolean(releaseCatalog)).toBe(true); await releaseCatalog!(); } };
}

async function layoutMetrics(page: Page) {
  return page.evaluate(() => {
    const stage = document.querySelector<HTMLElement>('[data-slot="map-stage"][data-view="map"]')!;
    const canvas = stage.querySelector<HTMLElement>('[role="application"]')!;
    const rect = stage.getBoundingClientRect();
    const canvasRect = canvas.getBoundingClientRect();
    const chrome = Object.fromEntries(Object.entries({ navigation: ".rail", menu: ".rail-nav", main: ".main", pageHeader: ".page-header-wrap", title: ".page-title", workspace: ".weather-workbench", filters: '[data-slot="map-filter-panel"]', viewHeader: ".kt-map-view-header", tabs: ".kt-map-view-tabs", readout: ".kt-map-readout", coordinates: ".kt-map-coordinates" }).map(([name, selector]) => {
      const element = document.querySelector<HTMLElement>(selector)!;
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return [name, { x: box.x, y: box.y, width: box.width, height: box.height, padding: style.padding, margin: style.margin, fontSize: style.fontSize }];
    }));
    return { viewport: { width: innerWidth, height: innerHeight }, stage: { x: rect.x, y: rect.y, documentTop: rect.y + scrollY, width: rect.width, height: rect.height, bottom: rect.bottom }, canvas: { width: canvasRect.width, height: canvasRect.height }, chrome, scrollY, documentHeight: document.documentElement.scrollHeight };
  });
}

for (const viewport of [{ width: 375, height: 812 }, { width: 320, height: 568 }, { width: 1440, height: 900 }, { width: 812, height: 375 }]) test.describe(`${viewport.width}×${viewport.height} 작업면`, () => {
  test.use({ viewport, hasTouch: viewport.width !== 1440 });
  test("남은 지도 높이·접기·목록과 상세", async ({ page }, testInfo) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    const { queries } = await openWorkspace(page);
    const stage = page.locator('[data-slot="map-stage"]:visible');
    await expect.poll(async () => (await stage.boundingBox())?.height ?? 0).toBeGreaterThan(0);
    const metrics = await layoutMetrics(page);
    await testInfo.attach("초기 초점 이동 전 지도 치수", { body: JSON.stringify(metrics), contentType: "application/json" });
    await page.screenshot({ path: `test-results/weather-workspace-${testInfo.project.name}-${viewport.width}-${viewport.height}-initial.png`, fullPage: true });
    if (viewport.width === 375) expect(metrics.stage.documentTop).toBeLessThanOrEqual(240);
    if (viewport.width === 1440) expect(Math.abs(viewport.height - metrics.stage.bottom)).toBeLessThanOrEqual(24);
    expect(metrics.stage.height).toBeGreaterThan(100);
    expect(Math.abs(metrics.canvas.height - metrics.stage.height)).toBeLessThanOrEqual(2);
    expect(await page.locator('[role="application"]').getAttribute("data-vworld-key")).toBe("missing");
    if (viewport.width !== 1440) {
      await expect(page.locator(".maplibregl-ctrl-zoom-in")).toHaveCSS("width", "44px");
      await expect(page.locator(".maplibregl-ctrl-zoom-in")).toHaveCSS("height", "44px");
    }

    const toggle = page.getByRole("button", { name: /^지도 필터/ });
    const compactFilters = await toggle.isVisible();
    if (compactFilters) {
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await expect(page.getByRole("searchbox", { name: "위치 검색" })).not.toBeVisible();
      await expect(toggle).toHaveCSS("min-height", "44px");
      await toggle.click();
    }
    const search = page.getByRole("searchbox", { name: "위치 검색" });
    await expect(search).toBeVisible();
    await search.fill("지도 작업면");
    if (compactFilters) {
      await toggle.click();
      await expect(toggle).toContainText("검색 적용");
      await toggle.click();
      await expect(search).toHaveValue("지도 작업면");
      await search.fill("");
      await toggle.click();
    } else await search.fill("");
    await expect.poll(() => queries.some((url) => url.pathname.endsWith("/markers") && url.searchParams.getAll("kind").length === 6)).toBe(true);
    const readout = page.locator(".kt-map-coordinates");
    await expect(readout).toContainText("center");
    const beforePan = await readout.innerText();
    const canvas = await page.locator(".maplibregl-canvas").boundingBox();
    await page.mouse.move(canvas!.x + canvas!.width / 2, canvas!.y + canvas!.height / 2);
    await page.mouse.down();
    await page.mouse.move(canvas!.x + canvas!.width / 2 - Math.min(80, canvas!.width / 4), canvas!.y + canvas!.height / 2, { steps: 8 });
    await page.mouse.up();
    await expect(readout).not.toHaveText(beforePan);
    const camera = await readout.innerText();
    await page.getByRole("tab", { name: "목록", exact: true }).click();
    const listStage = page.locator('[data-slot="map-stage"]:visible');
    await expect(listStage).toHaveAttribute("data-view", "list");
    await expect(page.locator(".location-list")).not.toHaveCSS("overflow-y", "auto");
    const listScroller = listStage.locator('[data-slot="map-stage-list"]');
    expect(await listScroller.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
    await page.locator(".location-list").getByRole("button", { name: /시험 위치 79/ }).scrollIntoViewIfNeeded();
    await expect(page.locator(".location-list").getByRole("button", { name: /시험 위치 79/ })).toBeInViewport();
    await page.getByRole("tab", { name: "지도", exact: true }).click();
    await expect(readout).toHaveText(camera);
    await page.getByRole("tab", { name: "목록", exact: true }).click();
    await page.locator(".location-list").getByRole("button", { name: /지도 작업면 시험 위치/ }).click();
    const detail = page.getByRole("complementary", { name: "선택한 위치 상세" });
    await expect(detail).toBeVisible();
    await expect(detail.getByRole("button", { name: "상세 닫기", exact: true })).toBeInViewport();
    await expect(detail.getByText("호우주의보 · 주의", { exact: true })).toBeVisible();
    await expect(detail.locator(".metric-card.primary").getByText("20 °C", { exact: true })).toBeVisible();
    for (const content of [detail.getByText("최신", { exact: true }), detail.getByText("호우주의보 · 주의", { exact: true }), detail.locator(".metric-card.primary").getByText("20 °C", { exact: true })]) {
      await content.scrollIntoViewIfNeeded();
      await expect(content).toBeInViewport({ ratio: 0.9 });
    }
    await detail.locator('[data-slot="map-detail-body"]').evaluate((element) => { element.scrollTop = 0; });
    await detail.getByRole("button", { name: "상세 접기", exact: true }).click();
    await expect(detail.locator('[data-slot="map-detail-body"]')).toBeHidden();
    await detail.getByRole("button", { name: "상세 펼치기", exact: true }).click();
    await expect(detail.locator('[data-slot="map-detail-body"]')).toBeVisible();
    await detail.getByRole("button", { name: "상세 접기", exact: true }).click();
    await page.getByRole("tab", { name: "목록", exact: true }).click();
    await page.locator(".location-list").getByRole("button").filter({ has: page.getByText("시험 위치 1", { exact: true }) }).click();
    await expect(detail.getByRole("heading", { name: "시험 위치 1", exact: true })).toBeVisible();
    await expect(detail.locator('[data-slot="map-detail-body"]')).toBeVisible();
    await page.screenshot({ path: `test-results/weather-workspace-${testInfo.project.name}-${viewport.width}-${viewport.height}-detail.png`, fullPage: true });
    await detail.focus();
    await page.keyboard.press("Escape");
    await expect(detail).toHaveCount(0);
    await expect(compactFilters ? toggle : search).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
});

test.describe("반응형 필터 초점", () => {
  test.use({ viewport: { width: 1440, height: 900 }, hasTouch: true });
  test("출처 팝업의 기준점이 숨겨지면 메뉴를 닫고 보이는 필터 버튼으로 돌아간다", async ({ page }, testInfo) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    await openWorkspace(page);
    // 접근성 이름은 "날씨 소스 필터: <현재 선택>"이다.
    await page.getByRole("button", { name: /^날씨 소스 필터/ }).click();
    const menu = page.getByRole("menu", { name: "날씨 소스 선택", exact: true });
    await expect(menu).toBeVisible();
    await page.setViewportSize({ width: 375, height: 812 });
    await expect(menu).not.toBeVisible();
    const toggle = page.getByRole("button", { name: /^지도 필터/ });
    await expect(toggle).toBeFocused();
    await page.screenshot({ path: `test-results/weather-workspace-${testInfo.project.name}-375-source-popup-resize.png`, fullPage: true });
    await toggle.click();
    await page.getByRole("button", { name: /^날씨 소스 필터/ }).click();
    await expect(menu).toBeVisible();
    const search = page.getByRole("searchbox", { name: "위치 검색" });
    await search.click();
    await expect(menu).not.toBeVisible();
    await expect(search).toBeFocused();
    await toggle.click();
    await page.getByRole("button", { name: "지도 크게 보기", exact: true }).click();
    const workspace = page.locator('[data-slot="map-workspace"]');
    await toggle.click();
    const source = page.getByRole("button", { name: /^날씨 소스 필터/ });
    await source.click();
    await page.keyboard.press("Escape");
    await expect(menu).not.toBeVisible();
    await expect(source).toBeFocused();
    await expect(workspace).toHaveAttribute("data-expanded", "true");
    await toggle.click();
    await page.getByRole("button", { name: "원래 화면으로", exact: true }).click();
    await page.setViewportSize({ width: 1440, height: 900 });
    await source.click();
    await expect(menu).toBeVisible();
    await page.evaluate(() => window.addEventListener("resize", () => document.querySelector<HTMLButtonElement>('button[aria-label="날씨 지도 안내"]')!.focus(), { once: true }));
    await page.setViewportSize({ width: 375, height: 812 });
    await expect(menu).not.toBeVisible();
    await expect(page.getByRole("button", { name: "날씨 지도 안내", exact: true })).toBeFocused();
  });
  test("좁은 화면 전환은 숨은 검색에서 필터 버튼으로 초점을 돌리고 조건을 보존한다", async ({ page }) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    const { queries } = await openWorkspace(page);
    const search = page.getByRole("searchbox", { name: "위치 검색" });
    await page.getByRole("group", { name: "종류 필터" }).getByRole("button", { name: "특보", exact: true }).click();
    await search.fill("지도 작업면");
    await search.focus();
    await page.setViewportSize({ width: 375, height: 812 });
    const toggle = page.getByRole("button", { name: /^지도 필터/ });
    await expect(toggle).toBeFocused();
    await expect(search).not.toBeVisible();
    await expect(toggle).toContainText("종류 5개");
    await expect(toggle).toContainText("검색 적용");
    await toggle.click();
    await expect(search).toHaveValue("지도 작업면");
    await expect(page.getByRole("group", { name: "종류 필터" }).getByRole("button", { name: "특보", exact: true })).toHaveAttribute("aria-pressed", "false");
    await expect.poll(() => queries.some((url) => url.pathname.endsWith("/markers") && url.searchParams.getAll("kind").length === 5 && !url.searchParams.getAll("kind").includes("alert"))).toBe(true);
    await toggle.click();
    await page.setViewportSize({ width: 812, height: 375 });
    await expect(toggle).toBeVisible();
    const landscape = await layoutMetrics(page);
    expect(landscape.stage.height).toBeGreaterThan(100);
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(search).toBeVisible();
    await expect(search).toHaveValue("지도 작업면");
    await expect.poll(() => page.locator('[data-slot="map-filter-panel-body"]').evaluate((element) => element.contains(document.activeElement) && document.activeElement instanceof HTMLElement && document.activeElement.getClientRects().length > 0)).toBe(true);
    const restored = await layoutMetrics(page);
    expect(Math.abs(restored.canvas.height - restored.stage.height)).toBeLessThanOrEqual(2);
  });
});

test.describe("작은 화면 글자 확대", () => {
  test.use({ viewport: { width: 320, height: 568 }, hasTouch: true });
  test("글자200%의 지도 접근 치수", async ({ page }, testInfo) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    const controls = await openWorkspace(page, { delayedCatalog: true });
    await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
    const pending = await layoutMetrics(page);
    await testInfo.attach("320px 글자200% 초기 조회 중", { body: JSON.stringify(pending), contentType: "application/json" });
    await controls.releaseCatalog();
    await expect(page.getByText("위치 카탈로그를 불러오는 중…", { exact: true })).toHaveCount(0);
    const metrics = await layoutMetrics(page);
    await testInfo.attach("320px 글자200%", { body: JSON.stringify(metrics), contentType: "application/json" });
    await page.screenshot({ path: `test-results/weather-workspace-${testInfo.project.name}-320-text200.png`, fullPage: true });
    expect(metrics.stage.height).toBeGreaterThan(0);
    expect(Math.abs(pending.stage.y - metrics.stage.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(pending.stage.height - metrics.stage.height)).toBeLessThanOrEqual(1);
    const canvas = await page.locator(".maplibregl-canvas").elementHandle();
    const workspace = page.locator('[data-slot="map-workspace"]');
    const expand = page.getByRole("button", { name: "지도 크게 보기", exact: true });
    await expect(expand).toBeInViewport({ ratio: 1 });
    await expand.focus();
    await page.keyboard.press("Enter");
    await expect(workspace).toHaveAttribute("data-expanded", "true");
    await expect(page.locator(".rail")).not.toBeVisible();
    await expect(page.getByRole("heading", { name: "날씨 지도", exact: true })).not.toBeVisible();
    expect(await canvas!.evaluate((element) => element.isConnected)).toBe(true);
    const expanded = await layoutMetrics(page);
    await testInfo.attach("320px 글자200% 확장 지도", { body: JSON.stringify(expanded), contentType: "application/json" });
    await page.screenshot({ path: `test-results/weather-workspace-${testInfo.project.name}-320-text200-expanded.png`, fullPage: true });
    expect(expanded.stage.height).toBeGreaterThan(200);
    expect(Math.abs(expanded.canvas.height - expanded.stage.height)).toBeLessThanOrEqual(2);
    await page.getByRole("tab", { name: "목록", exact: true }).click();
    await page.locator(".location-list").getByRole("button", { name: /지도 작업면 시험 위치/ }).click();
    const detail = page.getByRole("complementary", { name: "선택한 위치 상세" });
    await expect(detail.locator(".metric-card.primary").getByText("20 °C", { exact: true })).toBeVisible();
    const detailMetrics = await detail.evaluate((element) => Object.fromEntries(Object.entries({ detail: element, header: element.querySelector('[data-slot="map-detail-header"]')!, body: element.querySelector('[data-slot="map-detail-body"]')!, close: element.querySelector('button[aria-label="상세 닫기"]')!, freshness: element.querySelector(".weather-current-status")!, alert: element.querySelector(".alert-panel")!, temperature: element.querySelector(".metric-card.primary")!, zoomIn: document.querySelector(".maplibregl-ctrl-zoom-in")!, zoomOut: document.querySelector(".maplibregl-ctrl-zoom-out")! }).map(([name, node]) => {
      const box = node.getBoundingClientRect();
      return [name, { x: box.x, y: box.y, width: box.width, height: box.height, scrollHeight: node.scrollHeight, clientHeight: node.clientHeight }];
    })));
    await testInfo.attach("320px 글자200% 상세 접근 치수", { body: JSON.stringify(detailMetrics), contentType: "application/json" });
    await page.screenshot({ path: `test-results/weather-workspace-${testInfo.project.name}-320-text200-detail.png`, fullPage: true });
    await expect(detail.getByRole("button", { name: "상세 닫기", exact: true })).toBeInViewport({ ratio: 1 });
    expect(detailMetrics.body.height).toBeGreaterThan(0);
    expect(detailMetrics.detail.x + detailMetrics.detail.width).toBeLessThanOrEqual(detailMetrics.zoomIn.x);
    expect([detailMetrics.zoomIn.width, detailMetrics.zoomIn.height]).toEqual([44, 44]);
    for (const content of [detail.getByText("최신", { exact: true }), detail.getByText("호우주의보 · 주의", { exact: true }), detail.locator(".metric-card.primary").getByText("20 °C", { exact: true })]) {
      await content.scrollIntoViewIfNeeded();
      await expect(content).toBeInViewport({ ratio: 0.9 });
    }
    await page.screenshot({ path: `test-results/weather-workspace-${testInfo.project.name}-320-text200-temperature.png`, fullPage: true });
    const info = detail.getByRole("button", { name: "위치·데이터 출처 안내", exact: true });
    await info.click();
    const popup = page.getByRole("dialog", { name: "위치·데이터 출처", exact: true });
    await expect(popup).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(popup).toHaveCount(0);
    await expect(detail).toBeVisible();
    await expect(workspace).toHaveAttribute("data-expanded", "true");
    await detail.focus();
    await page.keyboard.press("Escape");
    await expect(detail).toHaveCount(0);
    await expect(workspace).toHaveAttribute("data-expanded", "true");
    const camera = await page.locator(".kt-map-coordinates").innerText();
    await page.keyboard.press("Escape");
    await expect(workspace).toHaveAttribute("data-expanded", "false");
    await expect(expand).toBeFocused();
    await expect(page.locator(".rail")).toBeVisible();
    await expect(page.locator(".kt-map-coordinates")).toHaveText(camera);
    expect(await canvas!.evaluate((element) => element.isConnected)).toBe(true);
    await expand.click();
    await page.getByRole("button", { name: "원래 화면으로", exact: true }).click();
    await expect(workspace).toHaveAttribute("data-expanded", "false");
    await expect(expand).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
});

test.describe("초기 조회 상태", () => {
  test.use({ viewport: { width: 375, height: 812 }, hasTouch: true });
  test("출처 조회 실패 안내가 목록 첫행을 가리지 않는다", async ({ page }, testInfo) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    await page.route("**/req/wmts/1.0.0/layout-tile-fixture-only/**", (route) => route.fulfill({ contentType: "image/png", body: readFileSync("e2e/fixtures/vworld-tile.png") }));
    await openWorkspace(page, { failedProviders: true });
    await page.getByRole("tab", { name: "목록", exact: true }).click();
    const warning = page.getByText("소스 목록을 불러오지 못했습니다. 페이지를 새로고침해 주세요.", { exact: true });
    await expect(warning).toBeVisible();
    const first = page.locator(".location-list").getByRole("button", { name: /지도 작업면 시험 위치/ });
    const hit = await first.evaluate((element) => {
      const box = element.getBoundingClientRect();
      const target = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
      return { hit: element.contains(target), firstRow: { top: box.top, bottom: box.bottom }, blockingClass: target?.className, blockingText: target?.textContent?.slice(0, 100) };
    });
    await testInfo.attach("출처 오류와 첫행 hit-test", { body: JSON.stringify(hit), contentType: "application/json" });
    await page.screenshot({ path: `test-results/weather-workspace-${testInfo.project.name}-375-source-error-list.png`, fullPage: true });
    expect(hit.hit).toBe(true);
    await first.click();
    const detail = page.getByRole("complementary", { name: "선택한 위치 상세" });
    await expect(detail.getByRole("heading", { name: "지도 작업면 시험 위치", exact: true })).toBeVisible();
    await detail.getByRole("button", { name: "상세 닫기", exact: true }).click();
    await page.getByRole("tab", { name: "목록", exact: true }).click();
    await expect(warning).toBeInViewport();
    await expect(first).toBeInViewport();
    await expect(page.locator(".location-list")).not.toHaveCSS("overflow-y", "auto");
  });
  for (const state of ["loading", "empty", "failed"] as const) test(`${state}에서도 지도 작업면 유지`, async ({ page }, testInfo) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    const controls = await openWorkspace(page, { delayedCatalog: state === "loading", emptyCatalog: state === "empty", failedCatalog: state === "failed" });
    const message = state === "loading" ? "위치 카탈로그를 불러오는 중…" : state === "empty" ? "활성화된 위치가 없습니다." : "시험 위치 카탈로그 조회 실패";
    await expect(page.getByText(message, { exact: true })).toBeVisible();
    await expect(page.locator(".kt-map-coordinates")).toContainText("center");
    const before = await layoutMetrics(page);
    expect(before.stage.height).toBeGreaterThan(100);
    await page.screenshot({ path: `test-results/weather-workspace-${testInfo.project.name}-375-${state}.png`, fullPage: true });
    if (state === "loading") {
      await controls.releaseCatalog();
      await expect(page.getByText(message, { exact: true })).toHaveCount(0);
      const after = await layoutMetrics(page);
      expect(Math.abs(before.stage.y - after.stage.y)).toBeLessThanOrEqual(1);
      expect(Math.abs(before.stage.height - after.stage.height)).toBeLessThanOrEqual(1);
    }
  });
});

for (const width of [414, 768]) test.describe(`${width}px 중간 폭 스모크`, () => {
  test.use({ viewport: { width, height: 896 }, hasTouch: true });
  test("초기 지도 치수·필터·상세 닫기", async ({ page }, testInfo) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    await openWorkspace(page);
    const metrics = await layoutMetrics(page);
    await testInfo.attach(`${width}px 초기 치수`, { body: JSON.stringify(metrics), contentType: "application/json" });
    expect(metrics.stage.height).toBeGreaterThan(100);
    expect(metrics.stage.bottom).toBeLessThanOrEqual(896);
    const toggle = page.getByRole("button", { name: /^지도 필터/ });
    if (await toggle.isVisible()) {
      await toggle.click();
      await expect(page.getByRole("searchbox", { name: "위치 검색" })).toBeVisible();
      await toggle.click();
    }
    await page.getByRole("tab", { name: "목록", exact: true }).click();
    await page.locator(".location-list").getByRole("button", { name: /지도 작업면 시험 위치/ }).click();
    const detail = page.getByRole("complementary", { name: "선택한 위치 상세" });
    await expect(detail.getByRole("button", { name: "상세 닫기", exact: true })).toBeInViewport();
    await page.screenshot({ path: `test-results/weather-workspace-${testInfo.project.name}-${width}-smoke.png`, fullPage: true });
    await detail.getByRole("button", { name: "상세 닫기", exact: true }).click();
    await expect(detail).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
});
