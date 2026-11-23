import { expect, test, type Page } from "@playwright/test";

// 지역 원천은 원천마다 job 하나다(수집 실패 감사 §2.5 P0 #3). 운영자는 Dagster 운영 화면에서
// 어느 원천이 실패했는지 job 이름만으로 알아야 한다. 메모리 fixture만 쓰며 운영 Dagster와 무관하다.
const now = Math.floor(Date.now() / 1000);
const REGIONAL = [
  ["krforest_mountain_job", "twice_daily_krforest_mountain", "30 5,6,8,17,18,20 * * *"],
  ["khoa_beach_index_job", "twice_daily_khoa_beach_index", "40 5,6,8,17,18,20 * * *"],
  ["krex_restarea_job", "twice_daily_krex_restarea", "45 5,6,8,17,18,20 * * *"],
  ["krforest_dust_job", "twice_daily_krforest_dust", "50 5,6,8,17,18,20 * * *"],
] as const;
const RUNS = [
  { runId: "c0ffee00-0000-4000-8000-000000000001", status: "FAILURE", jobName: "krex_restarea_job", startTime: now - 900, endTime: now - 600, tags: [] },
  { runId: "c0ffee00-0000-4000-8000-000000000002", status: "SUCCESS", jobName: "khoa_beach_index_job", startTime: now - 1200, endTime: now - 1000, tags: [] },
];

async function routeDagster(page: Page) {
  await page.route("**/api/dagster/graphql", async (route) => {
    const { operationName } = route.request().postDataJSON() as { operationName: string };
    if (operationName === "WeatherDagsterRunFailure") {
      await route.fulfill({ json: { data: { runsOrError: { __typename: "Runs", results: [{ eventConnection: {
        hasMore: false, cursor: "end", events: [
          { __typename: "ExecutionStepFailureEvent", stepKey: "krex_restarea_sync", message: "KrexConnectionError: [Errno -5] No address associated with hostname", diagnostic: true },
          { __typename: "RunFailureEvent", message: "Execution of run for \"krex_restarea_job\" failed." },
        ],
      } }] } } } });
      return;
    }
    await route.fulfill({ json: { data: {
      repositoryOrError: {
        __typename: "Repository", name: "__repository__", location: { name: "kortravelweather_dagster.definitions" },
        schedules: REGIONAL.map(([job, name, cron]) => ({ name, cronSchedule: cron, pipelineName: job, scheduleState: { status: "RUNNING" } })),
        jobs: REGIONAL.map(([job]) => ({ name: job })),
        assetNodes: [],
      },
      runsOrError: { __typename: "Runs", results: RUNS },
      activeRuns: { __typename: "Runs", results: [] },
    } } });
  });
}

async function login(page: Page) {
  await page.goto("/login?next=/admin/dagster");
  await page.locator('input[name="username"]').fill("admin");
  await page.locator('input[name="password"]').fill(process.env.E2E_UI_PASSWORD!);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect.poll(() => new URL(page.url()).pathname, { timeout: 30_000 }).toBe("/admin/dagster");
}

test("지역 원천 job은 원천 이름으로 보이고 실패 원인이 그 원천에 붙는다", async ({ page }) => {
  await routeDagster(page);
  await login(page);
  const failed = page.getByRole("button", { name: /실행 상세: 고속도로 휴게소 날씨 수집/ });
  await expect(failed.first()).toBeVisible({ timeout: 30_000 });
  // 묶음 job 이름(분리 전)은 새 실행 목록 어디에도 없다.
  await expect(page.getByText("지역별(해수욕장·산·고속도로) 날씨 수집", { exact: false })).toHaveCount(0);
  await failed.first().click();
  const detail = page.getByRole("region", { name: "선택한 실행 상세" });
  await expect(detail.getByRole("heading", { name: "고속도로 휴게소 날씨 수집" })).toBeVisible();
  await expect(detail.getByText("KrexConnectionError", { exact: false }).first()).toBeVisible();
});
