import { expect, test } from "@playwright/test";

// 관리 지도 호출 줄이기(호출 검토 B8·B12) fixture다. 실제 API·DB 없이 BFF로 가는 요청 모양만 본다.
test("카탈로그 나머지 페이지 병렬 수신·화면 간 캐시와 compact 마커 요청", async ({ page }) => {
  test.setTimeout(90_000);
  test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
  await page.setViewportSize({ width: 1440, height: 1000 });
  const total = 2300;
  const location = (index: number) => ({
    location_id: `budget-${index}`, name: `호출 시험 위치 ${index}`,
    latitude: 36.2 + (index % 50) * 0.01, longitude: 127.8 + Math.floor(index / 50) * 0.01,
    nx: null, ny: null, region_code: null, enabled: true, metadata: {},
  });
  const catalogOffsets: number[] = [];
  const markerQueries: URLSearchParams[] = [];
  let openRestPages = 0;
  let maximumOpenRestPages = 0;
  let releaseRest!: () => void;
  const restReleased = new Promise<void>((resolve) => { releaseRest = resolve; });
  await page.route("**/api/weather/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/v1/weather/locations")) {
      const offset = Number(url.searchParams.get("offset"));
      catalogOffsets.push(offset);
      if (offset > 0) {
        // 첫 페이지 뒤 두 페이지가 모두 도착할 때까지 응답하지 않는다. 순차 수신이면 두 번째가 오지 않는다.
        openRestPages += 1;
        maximumOpenRestPages = Math.max(maximumOpenRestPages, openRestPages);
        if (openRestPages === 2) releaseRest();
        await restReleased;
        openRestPages -= 1;
      }
      const count = Math.min(1000, total - offset);
      await route.fulfill({ json: {
        data: Array.from({ length: count }, (_, index) => location(offset + index)),
        meta: { page: { limit: 1000, offset, returned: count, total } },
      } });
      return;
    }
    if (url.pathname.endsWith("/markers")) {
      markerQueries.push(url.searchParams);
      await route.fulfill({ json: { data: url.searchParams.getAll("location_id").map((id) => ({
        location_id: id, latest: [], forecast: [], alerts: [], measurement_point: null,
      })), meta: {} } });
      return;
    }
    await route.fulfill({ json: { data: [], meta: {} } });
  });
  await page.goto("/login?next=/weather");
  await page.locator('input[name="username"]').fill("admin");
  await page.locator('input[name="password"]').fill(process.env.E2E_UI_PASSWORD!);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page).toHaveURL(/\/weather$/, { timeout: 30_000 });
  await expect(page.getByText(`활성 위치 카탈로그 ${total}곳`)).toBeVisible({ timeout: 30_000 });
  expect([...catalogOffsets].sort((a, b) => a - b)).toEqual([0, 1000, 2000]);
  expect(maximumOpenRestPages).toBe(2);

  // 지도 마커는 compact 응답으로 받는다.
  await expect.poll(() => markerQueries.length).toBeGreaterThan(0);
  expect(markerQueries.every((query) => query.get("view") === "compact")).toBe(true);

  // 다른 화면에 갔다가 돌아와도 신선한 동안은 카탈로그를 다시 받지 않는다(화면 사이 캐시).
  await page.getByRole("link", { name: "데이터셋" }).first().click();
  await expect(page).toHaveURL(/\/datasets$/);
  await page.getByRole("link", { name: "날씨 지도" }).first().click();
  await expect(page).toHaveURL(/\/weather$/);
  await expect(page.getByText(`활성 위치 카탈로그 ${total}곳`)).toBeVisible();
  expect(catalogOffsets).toHaveLength(3);
});
