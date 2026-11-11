import { expect, test, type Page } from "@playwright/test";

async function login(page: Page, destination: string) {
  await page.goto(`/login?next=${destination}`);
  await page.locator('input[name="username"]').fill("admin");
  await page
    .locator('input[name="password"]')
    .fill(process.env.E2E_UI_PASSWORD!);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${destination}$`));
}

const location = {
  location_id: "seoul",
  name: "서울",
  latitude: 37.5665,
  longitude: 126.978,
  nx: 60,
  ny: 127,
  enabled: true,
  region_code: null,
  metadata: {},
};

test("RHF·Zod 유효성 검사와 공통 위치 표·상태 변경", async ({ page }) => {
  test.skip(!process.env.E2E_UI_PASSWORD);
  let created: Record<string, unknown> | undefined;
  let enabled = true;
  await page.route("**/api/weather/v1/admin/locations**", async (route) => {
    const request = route.request();
    if (request.method() === "POST") created = request.postDataJSON();
    if (request.method() === "PATCH") enabled = request.postDataJSON().enabled;
    await route.fulfill({
      json: {
        data:
          request.method() === "GET" ? [{ ...location, enabled }] : location,
        meta: { page: { total: 1 } },
      },
    });
  });
  await login(page, "/locations");
  const table = page.getByRole("table", { name: "등록된 위치 목록" });
  await expect(
    table.getByRole("columnheader", { name: "위치", exact: true }),
  ).toBeVisible();
  await expect(table).not.toContainText("<tr>");
  await table.getByRole("button", { name: "비활성화", exact: true }).click();
  await expect(table.getByText("사용 안 함", { exact: true })).toBeVisible();
  await page.getByLabel("위치 ID", { exact: true }).fill("new-seoul");
  await page.getByLabel("이름", { exact: true }).fill("새 서울");
  await page.getByLabel("위도", { exact: true }).fill("91");
  await page.getByRole("button", { name: "위치 추가", exact: true }).click();
  await expect(page.locator(".location-create-form").getByRole("alert")).toContainText("유효한 좌표");
  expect(created).toBeUndefined();
  await page.getByLabel("위도", { exact: true }).fill("37.5");
  await page.getByRole("button", { name: "위치 추가", exact: true }).click();
  await expect.poll(() => created?.latitude).toBe(37.5);
  expect(created?.enabled).toBe(true);
  await expect(page.getByLabel("위치 ID", { exact: true })).toHaveValue("");
});

test("Zustand 실행 확장과 공통 원본 표는 해당 행 바로 아래 표시된다", async ({
  page,
}) => {
  test.skip(!process.env.E2E_UI_PASSWORD);
  const run = {
    run_id: "fixture-run-id",
    provider: "python-kma-api",
    dataset_key: "nowcast",
    status: "success",
    started_at: "2026-11-11T00:00:00Z",
    finished_at: null,
    heartbeat_at: null,
    error: null,
    values_loaded: 10,
    locations_total: 1,
    grids_fetched: 1,
    mid_groups_fetched: 0,
    requests_fetched: 1,
  };
  await page.route("**/api/weather/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const data = path.endsWith("/sources")
      ? [
          {
            source_record_key: "source-full-key",
            provider: run.provider,
            dataset_key: "nowcast",
            source_entity_id: "seoul",
            row_count: 10,
            fetched_at: run.started_at,
          },
        ]
      : path.endsWith("/providers")
        ? [
            {
              provider: run.provider,
              label: "기상청",
              datasets: [{ key: "nowcast", label: "실황" }],
            },
          ]
        : [run];
    return route.fulfill({ json: { data, meta: {} } });
  });
  await login(page, "/sync-runs");
  const table = page.getByRole("table", {
    name: "수집 실행 목록",
    exact: true,
  });
  const expand = table.getByRole("button", { name: /fixture-/ });
  await expand.click();
  await expect(expand).toHaveAttribute("aria-expanded", "true");
  const details = page.getByRole("region", { name: run.run_id, exact: true });
  await details.getByRole("button", { name: /원본 데이터/ }).click();
  await expect(
    details
      .getByRole("table", { name: "실행 원본 데이터" })
      .getByText("source-full-key"),
  ).toBeVisible();
  expect(
    await details.evaluate(
      (element) => element.closest("tr")?.previousElementSibling?.textContent,
    ),
  ).toContain("기상청");
  await expand.click();
  await expect(details).toHaveCount(0);
});


test("시간별 공통 표와 기온 그래프의 열 정렬·Pretendard 실제 로드", async ({ page }) => {
  test.skip(!process.env.E2E_UI_PASSWORD);
  await page.setViewportSize({ width: 375, height: 812 });
  const now = new Date(); now.setUTCMinutes(0, 0, 0);
  const value = { value_id: "current", location_id: location.location_id, provider: "python-kma-api",
    dataset_key: "short", forecast_style: "short", metric_key: "TMP", metric_name: "기온", value_number: 0,
    value_text: null, unit: "deg_c", target_at: now.toISOString(), issued_at: now.toISOString(), collected_at: now.toISOString() };
  const forecast = Array.from({ length: 24 }, (_, index) => ({ ...value, value_id: `hour-${index}`,
    target_at: new Date(now.getTime() + index * 3600000).toISOString(), value_number: index - 2 }));
  await page.route("**/api/weather/**", route => {
    const path = new URL(route.request().url()).pathname;
    const data = path.endsWith("/locations") ? [location]
      : path.endsWith("/providers") ? [{ provider: value.provider, label: "기상청", datasets: [{ key: "short", label: "단기예보" }] }]
      : path.endsWith("/markers") ? [{ location_id: location.location_id, latest: [value], forecast, alerts: [] }]
      : path.endsWith("/forecast") ? forecast : [value];
    return route.fulfill({ json: { data, meta: {} } });
  });
  await login(page, "/weather");
  await page.getByRole("tab", { name: "목록", exact: true }).click();
  await page.locator(".location-list").getByRole("button", { name: /서울/ }).click();
  const detail = page.getByRole("complementary", { name: "선택한 위치 상세" });
  const table = detail.getByRole("table", { name: "한국 시간 기준 기온과 강수·습도·바람 예보" });
  await expect(table.getByRole("columnheader")).toHaveCount(25);
  await table.scrollIntoViewIfNeeded();
  const alignment = await detail.evaluate(element => {
    const graph = element.querySelector<SVGSVGElement>(".weather-temperature-graph")!;
    const headers = [...element.querySelectorAll<HTMLTableCellElement>(".weather-hourly th")];
    const circles = [...graph.querySelectorAll("circle")];
    const bounds = graph.getBoundingClientRect();
    return circles.map((circle, index) => Math.abs(bounds.x + circle.cx.baseVal.value
      - (headers[index + 1].getBoundingClientRect().x + headers[index + 1].getBoundingClientRect().width / 2)));
  });
  expect(Math.max(...alignment)).toBeLessThanOrEqual(1);
  await expect.poll(() => page.evaluate(() => [...document.fonts].some(font => font.family.includes("Pretendard") && font.status === "loaded"))).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/weather-common-hourly-375.png", fullPage: true });
});
