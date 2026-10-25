import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

// 전용 공개 fixture 키의 모든 타일 요청을 가로챈다. 운영 VWorld/API 키를 사용하지 않는다.
const tileImage = readFileSync("e2e/fixtures/vworld-tile.png");
const tileFailure = "지도 표시 오류 · 목록 보기";

for (const { recover, initialFailure, moveAway, label } of [
  { recover: true, initialFailure: false, moveAway: null, label: "최초 load 뒤 타일 503→성공 회복" },
  { recover: true, initialFailure: false, moveAway: "pan", label: "실패 타일을 벗어난 정상 범위로 pan 회복" },
  { recover: true, initialFailure: false, moveAway: "zoom", label: "실패 타일과 다른 zoom의 정상 타일로 회복" },
  { recover: false, initialFailure: false, moveAway: null, label: "최초 load 뒤 타일 지속503" },
  { recover: false, initialFailure: true, moveAway: null, label: "최초 타일부터 지속503" },
]) test(label, async ({ page }, testInfo) => {
  test.skip(!process.env.E2E_UI_PASSWORD || !process.env.E2E_TILE_FIXTURE, "전용 타일 fixture 서버 필요");
  let failing = initialFailure;
  const failedTiles = new Set<string>();
  const retriedTiles = new Set<string>();
  const successfulTiles = new Set<string>();
  let pending = 0;
  await page.route("**/req/wmts/1.0.0/layout-tile-fixture-only/**", async (route) => {
    pending += 1;
    const key = new URL(route.request().url()).pathname.split("/").slice(-3).join("/");
    const status = failing ? 503 : 200;
    if (failing) failedTiles.add(key);
    else if (failedTiles.has(key)) retriedTiles.add(key);
    if (!failing) successfulTiles.add(key);
    try {
      await route.fulfill({ status, contentType: failing ? "text/plain" : "image/png", body: failing ? "fixture unavailable" : tileImage, headers: { "cache-control": "no-store", "access-control-allow-origin": "*" } });
    } finally { pending -= 1; }
  });
  const stamp = new Date().toISOString();
  const location = { location_id: "tile-fixture", name: "타일 회복 시험", latitude: 36.2, longitude: 127.8, enabled: true, nx: null, ny: null, region_code: null, metadata: {} };
  const value = { value_id: "temperature", location_id: location.location_id, provider: "python-kma-api", dataset_key: "nowcast", weather_domain: "weather", forecast_style: "nowcast", metric_key: "T1H", metric_name: "기온", value_number: 20, value_text: null, unit: "°C", target_at: stamp, collected_at: stamp, source_record_key: "tile-fixture-source" };
  await page.route("**/api/weather/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (recover && pathname.endsWith("/latest")) return route.fulfill({ status: 503, json: { detail: "시험 날씨 요청 실패" } });
    const data = pathname.endsWith("/providers") ? [{ provider: "python-kma-api", label: "기상청", datasets: [{ key: "nowcast", label: "실황" }] }]
      : pathname.endsWith("/locations") ? [location]
      : pathname.endsWith("/markers") ? [{ location_id: location.location_id, latest: [value], forecast: [], alerts: [] }]
      : pathname.endsWith("/latest") ? [value] : [];
    return route.fulfill({ json: { data, meta: {} } });
  });
  await page.goto("/login?next=/weather");
  await page.locator('input[name="username"]').fill("admin");
  await page.locator('input[name="password"]').fill(process.env.E2E_UI_PASSWORD!);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByRole("button", { name: /타일 회복 시험:.*날씨 보기/ })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('[role="application"]')).toHaveAttribute("data-vworld-key", "configured");
  if (!initialFailure) {
    await expect(page.getByText(tileFailure, { exact: true })).toHaveCount(0);
    failing = true;
    const readout = page.locator(".kt-map-coordinates");
    const initial = await readout.innerText();
    await page.locator(".maplibregl-ctrl-zoom-in").click();
    await expect(readout).not.toHaveText(initial);
    await expect(page.getByText(tileFailure, { exact: true })).toBeVisible();
    await expect.poll(() => pending).toBe(0);
    const failedCamera = await readout.innerText();
    if (recover) failing = false;
    if (moveAway === "pan") {
      const canvas = (await page.locator(".maplibregl-canvas").boundingBox())!;
      for (let step = 0; step < 3; step += 1) {
        const before = await readout.innerText();
        await page.mouse.move(canvas.x + 20, canvas.y + canvas.height / 2);
        await page.mouse.down();
        await page.mouse.move(canvas.x + canvas.width - 20, canvas.y + canvas.height / 2, { steps: 15 });
        await page.mouse.up();
        await expect(readout).not.toHaveText(before);
      }
      await expect.poll(() => pending).toBe(0);
      await testInfo.attach("실패 범위를 벗어난 pan", { body: JSON.stringify({ camera: await readout.innerText(), failed: [...failedTiles], retried: [...retriedTiles], successful: [...successfulTiles] }), contentType: "application/json" });
    } else if (moveAway === "zoom") {
      await page.locator(".maplibregl-ctrl-zoom-in").click();
      await expect(readout).not.toHaveText(failedCamera);
    } else {
      await page.locator(".maplibregl-ctrl-zoom-out").click();
      await expect(readout).not.toHaveText(failedCamera);
      await page.locator(".maplibregl-ctrl-zoom-in").click();
      await expect(readout).toHaveText(failedCamera);
    }
  }
  await expect.poll(() => pending).toBe(0);
  if (recover) {
    if (!moveAway) await expect.poll(() => retriedTiles.size).toBeGreaterThan(0);
    await expect(page.getByText(tileFailure, { exact: true })).toHaveCount(0);
  } else {
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))));
    await expect(page.getByText(tileFailure, { exact: true })).toBeVisible();
  }
  await page.getByRole("tab", { name: "목록", exact: true }).click();
  await page.locator(".location-list").getByRole("button", { name: /타일 회복 시험/ }).click();
  const detail = page.getByRole("complementary", { name: "선택한 위치 상세" });
  await expect(recover ? detail.getByText("시험 날씨 요청 실패", { exact: true }) : detail.locator(".metric-card.primary").getByText("20 °C", { exact: true })).toBeVisible();
  if (!recover) await expect(page.getByText(tileFailure, { exact: true })).toBeVisible();
  await testInfo.attach("타일 주입 결과", { body: JSON.stringify({ failedTiles: failedTiles.size, retriedTiles: retriedTiles.size, pending }), contentType: "application/json" });
  await page.screenshot({ path: `test-results/weather-tiles-${testInfo.project.name}-${moveAway ? `${moveAway}-recovered` : recover ? "recovered" : initialFailure ? "initial-persistent" : "persistent"}.png`, fullPage: true });
});
