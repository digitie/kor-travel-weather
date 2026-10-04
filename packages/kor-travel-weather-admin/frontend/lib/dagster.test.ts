import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DagsterRun,
  STALLED_RUN_THRESHOLD_SECONDS,
  formatElapsed,
  getDagsterSnapshot,
  isStalledRun,
  jobLabel,
  runElapsedSeconds,
} from "./dagster";
import { DAGSTER_LOCATION_NAME, scopedDagsterRequest } from "./dagster-scope";

function run(overrides: Partial<DagsterRun>): DagsterRun {
  return {
    runId: "11111111-1111-1111-1111-111111111111",
    status: "STARTED",
    jobName: "open_meteo_weather_job",
    startTime: 0,
    endTime: null,
    errorMessage: null,
    ...overrides,
  };
}

describe("jobLabel", () => {
  it("translates a known job name into its Korean description", () => {
    expect(jobLabel("kma_short_forecast_job")).toBe("기상청 단기예보 수집");
  });

  it("names the provider for each external collection job", () => {
    // One job per external provider: the label has to say which source is
    // late, which the single "external weather" job it replaced could not.
    expect(jobLabel("open_meteo_weather_job")).toBe("Open-Meteo 날씨 수집");
    expect(jobLabel("accuweather_weather_sync")).toBe("AccuWeather 날씨 수집");
  });

  it("translates a known step name the same way as a job name", () => {
    expect(jobLabel("krforest_dust_sync")).toBe("청정넷 미세먼지 수집");
  });

  it("falls back to the raw name for anything this project does not define", () => {
    expect(jobLabel("some_future_job_nobody_labeled_yet")).toBe(
      "some_future_job_nobody_labeled_yet",
    );
  });
});

describe("runElapsedSeconds", () => {
  it("is null for a run that already finished", () => {
    expect(runElapsedSeconds(run({ status: "SUCCESS", startTime: 0 }), 100)).toBeNull();
  });

  it("is null for a run with no recorded start time", () => {
    expect(runElapsedSeconds(run({ status: "STARTED", startTime: null }), 100)).toBeNull();
  });

  it("is the gap between start and now for a run still in progress", () => {
    expect(runElapsedSeconds(run({ status: "STARTED", startTime: 40 }), 100)).toBe(60);
  });
});

describe("isStalledRun", () => {
  it("is false just under the threshold", () => {
    const startTime = 1000;
    const now = startTime + STALLED_RUN_THRESHOLD_SECONDS - 1;
    expect(isStalledRun(run({ status: "STARTED", startTime }), now)).toBe(false);
  });

  it("is true at and past the threshold", () => {
    const startTime = 1000;
    const now = startTime + STALLED_RUN_THRESHOLD_SECONDS;
    expect(isStalledRun(run({ status: "STARTED", startTime }), now)).toBe(true);
  });

  it("is false for a run that is not STARTED, no matter how old", () => {
    const startTime = 0;
    const now = STALLED_RUN_THRESHOLD_SECONDS * 100;
    expect(isStalledRun(run({ status: "SUCCESS", startTime }), now)).toBe(false);
  });
});

describe("formatElapsed", () => {
  it("reads in whole minutes under an hour", () => {
    expect(formatElapsed(42 * 60)).toBe("42분");
  });

  it("reads in hours and minutes at an hour or more", () => {
    expect(formatElapsed(72 * 60)).toBe("1시간 12분");
  });

  it("calls out anything under a minute rather than reading as 0분", () => {
    expect(formatElapsed(30)).toBe("1분 미만");
  });
});

describe("getDagsterSnapshot", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function jsonResponse(payload: unknown): Response {
    return new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } });
  }

  it("sends only named operations, and every one is one the proxy accepts", async () => {
    const failedRunId = "22222222-2222-2222-2222-222222222222";
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      if (request.operationName === "WeatherDagsterRunFailure") {
        return jsonResponse({ data: { runsOrError: { __typename: "Runs", results: [{ eventConnection: { events: [{ __typename: "RunFailureEvent", message: "boom" }] } }] } } });
      }
      return jsonResponse({
        data: {
          repositoryOrError: {
            __typename: "Repository",
            name: "__repository__",
            location: { name: DAGSTER_LOCATION_NAME },
            schedules: [{ name: "hourly_airkorea_weather", cronSchedule: "10 * * * *", pipelineName: "airkorea_weather_job", scheduleState: { status: "RUNNING" } }],
            jobs: [{ name: "airkorea_weather_job" }],
            assetNodes: [],
          },
          runsOrError: { __typename: "Runs", results: [{ runId: failedRunId, status: "FAILURE", jobName: "airkorea_weather_job", startTime: 1, endTime: 2 }] },
        },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const snapshot = await getDagsterSnapshot(12);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/dagster/graphql", "/api/dagster/graphql"]);
    for (const [, init] of fetchMock.mock.calls) {
      const request = JSON.parse(String(init.body));
      expect(request).not.toHaveProperty("query");
      expect(scopedDagsterRequest(request).ok).toBe(true);
    }
    expect(snapshot.repositories).toHaveLength(1);
    expect(snapshot.repositories[0].locationName).toBe(DAGSTER_LOCATION_NAME);
    expect(snapshot.runs[0].errorMessage).toBe("boom");
  });

  it("follows bounded log pages without retaining earlier events", async () => {
    const failedRunId = "22222222-2222-2222-2222-222222222222";
    const cursors: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      if (request.operationName === "WeatherDagsterOverview") return jsonResponse({ data: {
        repositoryOrError: { __typename: "Repository", schedules: [], jobs: [], assetNodes: [] },
        runsOrError: { __typename: "Runs", results: [{ runId: failedRunId, status: "FAILURE" }] },
      } });
      cursors.push(request.variables.cursor);
      return jsonResponse({ data: { runsOrError: { results: [{ eventConnection:
        request.variables.cursor === null
          ? { events: [{ __typename: "LogMessageEvent" }], cursor: "page-2", hasMore: true }
          : { events: [{ __typename: "RunFailureEvent", message: "late failure" }], hasMore: false },
      }] } } });
    }));
    expect((await getDagsterSnapshot()).runs[0].errorMessage).toBe("late failure");
    expect(cursors).toEqual([null, "page-2"]);
    const query = scopedDagsterRequest({ operationName: "WeatherDagsterRunFailure", variables: { runId: failedRunId, cursor: "page-2" } });
    expect(query.ok).toBe(true);
    if (query.ok) expect(JSON.parse(query.body).query).toContain("limit: 1000");
  });

  it("reports a location the webserver does not serve instead of showing an empty page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          data: {
            repositoryOrError: { __typename: "RepositoryNotFoundError", message: "Could not find Repository kortravelweather_dagster.definitions.__repository__" },
            runsOrError: { __typename: "Runs", results: [] },
          },
        }),
      ),
    );
    await expect(getDagsterSnapshot()).rejects.toThrow("Could not find Repository");
  });

  it.each([
    ["PythonError", "psycopg2.OperationalError: connection refused"],
    ["InvalidPipelineRunsFilterError", "Invalid runs filter"],
  ])("reports a %s from the run query instead of showing no runs", async (typename, message) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          data: {
            repositoryOrError: { __typename: "Repository", name: "__repository__", location: { name: DAGSTER_LOCATION_NAME }, schedules: [], jobs: [], assetNodes: [] },
            runsOrError: { __typename: typename, message },
          },
        }),
      ),
    );
    await expect(getDagsterSnapshot()).rejects.toThrow(message);
  });
});
