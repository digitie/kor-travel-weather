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

// 같은 화면의 주소 갱신 패널은 이 요약 모양을 기대한다. fixture backend의 기본 응답
// (`{ data: [], meta: {} }`)이 그대로 들어가면 패널이 렌더 중 예외를 내고 Dagster 운영 화면
// 전체가 "UI 런타임 오류"로 바뀌어, 아래 실행 표가 아예 그려지지 않는다.
const ADDRESS_REFRESH_SUMMARY = {
  total: 0, due: 0, failed: 0, refresh_months: 6, configured: false, auto_enabled: false,
  in_progress: false, manual_cycle: false, last_resolved_at: null, items: [],
};

async function routeDagster(page: Page) {
  await page.route("**/api/address-refresh", (route) => route.fulfill({ json: ADDRESS_REFRESH_SUMMARY }));
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

// CI는 산출물을 올리지 않는다. 실패하면 화면 글자 일부를 오류 메시지에 붙여 로그만으로 원인을 본다.
async function withPageText(page: Page, check: () => Promise<void>) {
  try {
    await check();
  } catch (error) {
    const text = (await page.locator("body").innerText().catch(() => "")).slice(0, 2000);
    throw new Error(`${error instanceof Error ? error.message : String(error)}
--- page ---
${text}`);
  }
}

test("지역 원천 job은 원천 이름으로 보이고 실패 원인이 그 원천에 붙는다", async ({ page }) => {
  await routeDagster(page);
  await login(page);
  const failed = page.getByRole("button", { name: /실행 상세: 고속도로 휴게소 날씨 수집/ });
  // 로그인 대기(최대 30초)와 합쳐도 시험 상한 45초 안에 실패가 드러나게 한다.
  await withPageText(page, () => expect(failed.first()).toBeVisible({ timeout: 10_000 }));
  // 묶음 job 이름(분리 전)은 새 실행 목록 어디에도 없다.
  await expect(page.getByText("지역별(해수욕장·산·고속도로) 날씨 수집", { exact: false })).toHaveCount(0);
  await failed.first().click();
  const detail = page.getByRole("region", { name: "선택한 실행 상세" });
  await withPageText(page, async () => {
    await expect(detail.getByRole("heading", { name: "고속도로 휴게소 날씨 수집" })).toBeVisible();
    await expect(detail.getByText("KrexConnectionError", { exact: false }).first()).toBeVisible();
  });
});

test("공통 오류 로그 필터로 불러온 처리 내역을 전환한다", async ({ page }) => {
  await routeDagster(page);
  const event = (kind: string, title: string) => ({
    kind, title, time: null, step: null, detail: null, warning: false, counts: {},
  });
  await page.route("**/api/dagster/console", route => {
    const {operation, cursor} = route.request().postDataJSON();
    if (operation === "catalog") return route.fulfill({json: {
      jobs: [], schedules: [], sensors: [], capabilities: {}, checkedAt: null,
    }});
    expect(operation).toBe("detail");
    return route.fulfill({json: {
      ...RUNS[0], creationTime: null, queuedTime: null, canCancel: false, canRetry: false,
      steps: [], stepsSucceeded: 0, stepsFailed: 1, materializations: 0, stepsTruncated: false,
      checkedAt: null, cursor: cursor ? null : "next", hasMore: !cursor,
      events: cursor
        ? [event("RunFailureEvent", "다음 페이지 실패")]
        : [event("ExecutionStepStartEvent", "정상 처리")],
    }});
  });
  await login(page);
  await page.getByRole("button", {name: /실행 상세: 고속도로 휴게소 날씨 수집/}).first().click();
  const events = page.getByRole("list", {name: "안전한 처리 내역"});
  const filter = page.getByRole("button", {name: "오류 로그만 보기"});
  await expect(events).toContainText("정상 처리");
  await filter.click();
  await expect(filter).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("status").filter({hasText: "불러온 내역에 오류 로그가 없습니다."})).toBeVisible();
  await expect(filter).toBeFocused();
  await page.getByRole("button", {name: "다음 내역 보기"}).click();
  await expect(events).toContainText("다음 페이지 실패");
  await expect(events).not.toContainText("정상 처리");
  await expect(page.getByRole("status").filter({hasText: "오류 로그 1건"})).toBeAttached();
});
