import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DAGSTER_LOCATION_NAME } from "@/lib/dagster-scope";

const auth = vi.hoisted(() => ({ allowed: true }));
vi.mock("@/lib/session", () => ({
  adminUsername: () => "admin", SESSION_COOKIE: "weather_session",
  sessionSecret: () => "s".repeat(32),
  verifySessionValue: async () => auth.allowed ? "admin" : null,
  checkDurableSession: async () => "active",
}));
import { GET, POST } from "./route";

const requestId = "03147536-8f5c-41ec-a725-be2ca3b024f3";
const response = (value: unknown) => new Response(JSON.stringify({ data: value }), { status: 200 });
const runs = (values: object[] = []) => response({ runsOrError: { __typename: "Runs", results: values } });
function request(body: object = { requestId }, origin = "http://address.test") {
  return new NextRequest("http://address.test/api/address-refresh", { method: "POST",
    headers: { origin, "content-type": "application/json", cookie: "weather_session=session" },
    body: JSON.stringify(body) });
}
beforeEach(() => { auth.allowed = true; vi.stubEnv("WEATHER_UI_PUBLIC_ORIGIN", "http://address.test"); });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("주소 수동 갱신 BFF", () => {
  it("미인증·교차 Origin·임의 job 설정은 upstream 호출 전에 차단한다", async () => {
    const upstream = vi.fn(); vi.stubGlobal("fetch", upstream);
    auth.allowed = false;
    expect((await POST(request())).status).toBe(401);
    expect((await GET(new NextRequest("http://address.test/api/address-refresh"))).status).toBe(401);
    auth.allowed = true;
    expect((await POST(request({ requestId }, "https://elsewhere.test"))).status).toBe(403);
    expect((await POST(request({ requestId, job: "other_project" }))).status).toBe(422);
    expect((await POST(request({ requestId: "invalid" }))).status).toBe(422);
    expect(upstream).not.toHaveBeenCalled();
  });
  it("서버가 고정한 자기 location의 주소 job만 force=true로 예약한다", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(runs()).mockResolvedValueOnce(runs())
      .mockResolvedValueOnce(response({ launchRun: { __typename: "LaunchRunSuccess", run: { runId: requestId, status: "QUEUED" } } }));
    vi.stubGlobal("fetch", fetcher);
    const result = await POST(request());
    expect(result.status).toBe(202);
    expect((await result.json()).replayed).toBe(false);
    const payload = JSON.parse(fetcher.mock.calls[2][1].body);
    const params = payload.variables.executionParams;
    expect(params.selector).toEqual({ repositoryLocationName: DAGSTER_LOCATION_NAME,
      repositoryName: "__repository__", pipelineName: "address_refresh_job" });
    expect(params.runConfigData).toEqual({ ops: { refresh_addresses: { config: { force: true } } } });
    expect(params.executionMetadata.tags).toContainEqual({ key: "kortravel/address-request", value: requestId });
    expect(params.executionMetadata).not.toHaveProperty("runId");
    expect(result.headers.get("cache-control")).toBe("no-store, private");
  });
  it.each(["SUCCESS", "FAILURE", "CANCELED"])("이전 %s 요청을 새 예약 없이 재조회한다", async (status) => {
    const fetcher = vi.fn().mockResolvedValueOnce(runs([{ runId: requestId, status }]));
    vi.stubGlobal("fetch", fetcher);
    const result = await POST(request());
    expect(result.status).toBe(202);
    expect(await result.json()).toEqual({ runId: requestId, status, replayed: true });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const payload = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(payload.query).toContain("kortravel/address-request");
    expect(payload.variables).toEqual({ location: DAGSTER_LOCATION_NAME, requestId });
  });
  it("진행 중인 자동 실행을 수동 갱신 완료로 응답하지 않는다", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(runs()).mockResolvedValueOnce(runs([{ runId: requestId, status: "STARTED" }]));
    vi.stubGlobal("fetch", fetcher);
    expect((await POST(request())).status).toBe(409);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it.each([requestId, "03147536-8f5c-41ec-a725-be2ca3b024f4"])("동시 예약 %s를 차단하고 한 번만 launch한다", async (secondId) => {
    let release!: () => void;
    let entered!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const started = new Promise<void>(resolve => { entered = resolve; });
    let previousCalls = 0;
    let launches = 0;
    const fetcher = vi.fn(async (_url: unknown, init: RequestInit) => {
      const query = JSON.parse(String(init.body)).query as string;
      if (query.includes("mutation")) {
        launches += 1;
        return response({ launchRun: { __typename: "LaunchRunSuccess", run: { runId: requestId, status: "QUEUED" } } });
      }
      if (query.includes("kortravel/address-request")) {
        previousCalls += 1;
        if (previousCalls === 1) { entered(); await gate; }
      }
      return runs();
    });
    vi.stubGlobal("fetch", fetcher);
    const first = POST(request());
    await started;
    const second = await POST(request({ requestId: secondId }));
    release();
    expect((await first).status).toBe(202);
    expect(second.status).toBe(409);
    expect(launches).toBe(1);
  });
  it("원문 upstream 오류나 키를 응답하지 않는다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("secret-key-in-url")));
    const result = await POST(request());
    expect(result.status).toBe(502);
    expect(await result.text()).not.toContain("secret-key-in-url");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(runs()).mockResolvedValueOnce(runs())
      .mockResolvedValueOnce(response({ launchRun: { __typename: "LaunchRunSuccess", run: { runId: requestId, status: "QUEUED" } } })));
    expect((await POST(request())).status).toBe(202);
  });
});
