import { expect, test } from "@playwright/test";
import { ADDRESS_REFRESH_PREVIOUS_QUERY, DAGSTER_LOCATION_NAME } from "../lib/dagster-scope";

const base = process.env.E2E_BASE_URL ?? "http://localhost:24105";
const enabled = process.env.ADDRESS_REFRESH_NATIVE_DELAY_E2E === "1";
const local = ["localhost", "127.0.0.1"].includes(new URL(base).hostname);
test.skip(!enabled || !local, "전용 loopback 지연 proxy·native Dagster fixture가 필요합니다.");

test("응답 시간 초과 후 늦은 예약과 재시도가 실제 native 실행 하나를 공유한다", async ({ page }) => {
  test.setTimeout(45_000);
  await page.goto("/login?next=/admin/dagster");
  await page.locator('input[name="username"]').fill(process.env.E2E_UI_USER ?? "admin");
  await page.locator('input[name="password"]').fill(process.env.E2E_UI_PASSWORD ?? "");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.waitForURL("**/admin/dagster");
  await expect(page.getByRole("heading", { name: "주소 갱신", exact: true })).toBeVisible();
  const requestId = crypto.randomUUID();
  expect((await page.request.post("http://127.0.0.1:24602/arm", { data: {} })).ok()).toBe(true);
  const first = await page.request.post("/api/address-refresh", {
    headers: { origin: base }, data: { requestId },
  });
  expect(first.status()).toBe(502);
  const retry = await page.request.post("/api/address-refresh", {
    headers: { origin: base }, data: { requestId },
  });
  expect(retry.status()).toBe(202);
  const run = await retry.json();
  expect(run.status).toBe("QUEUED");
  await expect.poll(async () => (await (await page.request.get("http://127.0.0.1:24602/state")).json()).completed).toBe(1);
  const query = ADDRESS_REFRESH_PREVIOUS_QUERY.replace("limit: 1", "limit: 2");
  expect(query).not.toBe(ADDRESS_REFRESH_PREVIOUS_QUERY);
  const actual = await page.request.post("http://127.0.0.1:24102/graphql", { data: { query, variables: { requestId, location: DAGSTER_LOCATION_NAME } } });
  const result = await actual.json();
  expect(result.errors).toBeUndefined();
  expect(result.data.runsOrError.results).toEqual([{ runId: run.runId, status: "QUEUED" }]);
  const replay = await page.request.post("/api/address-refresh", {
    headers: { origin: base }, data: { requestId: requestId.toUpperCase() },
  });
  expect(replay.status()).toBe(202);
  expect(await replay.json()).toEqual({ runId: run.runId, status: "QUEUED", replayed: true });
});
