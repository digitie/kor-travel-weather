import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DAGSTER_REPOSITORY_TAG_VALUE } from "@/lib/dagster-scope";

import { POST } from "./route";

function post(body: unknown): NextRequest {
  return new NextRequest("http://127.0.0.1:14105/api/dagster/graphql", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

describe("POST /api/dagster/graphql", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("실패 원문과 metadata의 키·URL은 서버 응답에서 제외한다", async () => {
    const secret = "fixture-private-token";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ data: { runsOrError: { __typename: "Runs", results: [{ eventConnection: {
      hasMore: false, cursor: null, events: [{ __typename: "ExecutionStepFailureEvent", message: secret,
        error: { className: "AlertCollectionError", message: `ALERT_PAGE_METADATA https://example.invalid/?key=${secret}` },
        failureMetadata: { metadataEntries: [{ label: "stage", text: "page_validation" }, { label: "metadata_field", text: "pageNo" }, { label: "raw", text: secret }] },
      }],
    } }] } } })));
    const response = await POST(post({ operationName: "WeatherDagsterRunFailure", variables: { runId: "11111111-1111-1111-1111-111111111111", cursor: null } }));
    const body = await response.text();
    expect(response.status).toBe(200);
    expect(body).toContain("ALERT_PAGE_METADATA");
    expect(body).toContain("페이지 완결성 검사");
    expect(body).not.toContain(secret);
    expect(body).not.toContain("https://");
    expect(body).not.toContain('"error"');
  });

  it("never forwards a raw GraphQL document to the webserver", async () => {
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    const response = await POST(post({ query: "{ repositoriesOrError { __typename } }" }));
    expect(response.status).toBe(400);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("refuses a body that is not JSON", async () => {
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    const response = await POST(post("{ repositoriesOrError { __typename } }"));
    expect(response.status).toBe(400);
    expect(upstream).not.toHaveBeenCalled();
  });

  it.each(["__proto__", "constructor"])("answers a %s variable with 400, not 502", async (name) => {
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    const response = await POST(post(`{"operationName":"WeatherDagsterOverview","variables":{"limit":12,"${name}":1}}`));
    expect(response.status).toBe(400);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("forwards a named operation with the scope the server chose", async () => {
    vi.stubEnv("DAGSTER_UI_INTERNAL_URL", "http://127.0.0.1:14107/");
    const upstream = vi.fn(async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", upstream);
    const response = await POST(post({ operationName: "WeatherDagsterOverview", variables: { limit: 12 } }));
    expect(response.status).toBe(200);
    expect(upstream).toHaveBeenCalledTimes(1);
    const [url, init] = upstream.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:14107/graphql");
    const sent = JSON.parse(String(init.body));
    expect(sent.operationName).toBe("WeatherDagsterOverview");
    expect(sent.query).toContain("repositoryOrError(repositorySelector:");
    expect(sent.variables.repositoryTag).toBe(DAGSTER_REPOSITORY_TAG_VALUE);
  });
});
