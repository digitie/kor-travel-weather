import type { DagsterSnapshot } from "@kor-travel/ui/dagster-model";
export type { DagsterSchedule, DagsterRepository, DagsterRun, DagsterSnapshot } from "@kor-travel/ui/dagster-model";
export { runStatusLabel, runElapsedSeconds, STALLED_RUN_THRESHOLD_SECONDS, isStalledRun, formatElapsed, describeCron } from "@kor-travel/ui/dagster-model";

import type { DagsterOperationName, DagsterOperationRequest } from "@/lib/dagster-scope";
import { failureMessage, readBody } from "@/lib/http";

// External weather is one job per provider, so the label is the provider's own
// name -- the operator reading this page wants to know which source is late,
// not that "external" collection in the abstract is.
const EXTERNAL_PROVIDER_LABELS: Record<string, string> = {
  weatherapi: "WeatherAPI",
  openweathermap: "OpenWeatherMap",
  open_meteo: "Open-Meteo",
  visual_crossing: "Visual Crossing",
  tomorrow_io: "Tomorrow.io",
  weatherbit: "Weatherbit",
  weatherstack: "Weatherstack",
  accuweather: "AccuWeather",
  wttr_in: "wttr.in",
};

function externalLabels(suffix: string): Record<string, string> {
  return Object.fromEntries(
    Object.entries(EXTERNAL_PROVIDER_LABELS).map(([key, label]) => [
      `${key}${suffix}`,
      `${label} 날씨 수집`,
    ]),
  );
}

// dagster job/schedule/asset names are internal identifiers, not something an
// operator glancing at this page should have to already know by heart.
const JOB_LABELS: Record<string, string> = {
  kma_ultra_short_nowcast_job: "기상청 초단기실황 수집",
  kma_ultra_short_forecast_job: "기상청 초단기예보 수집",
  kma_short_forecast_job: "기상청 단기예보 수집",
  kma_mid_forecast_job: "기상청 중기예보 수집",
  kma_weather_alerts_job: "기상청 특보 수집",
  airkorea_weather_job: "에어코리아 대기질 수집",
  weather_retention_job: "보존 기간 만료 데이터 정리",
  regional_weather_job: "지역별(해수욕장·산·고속도로) 날씨 수집",
  ...externalLabels("_weather_job"),
};

const STEP_LABELS: Record<string, string> = {
  kma_ultra_short_nowcast_sync: "기상청 초단기실황 수집",
  kma_ultra_short_forecast_sync: "기상청 초단기예보 수집",
  kma_short_forecast_sync: "기상청 단기예보 수집",
  kma_mid_forecast_sync: "기상청 중기예보 수집",
  kma_weather_alerts_sync: "기상청 특보 수집",
  airkorea_weather_sync: "에어코리아 대기질 수집",
  weather_retention_purge: "보존 기간 만료 데이터 정리",
  khoa_beach_index_sync: "해수욕장 지수 수집",
  krforest_mountain_sync: "산악 날씨 수집",
  krforest_dust_sync: "청정넷 미세먼지 수집",
  krex_restarea_sync: "고속도로 휴게소 날씨 수집",
  ...externalLabels("_weather_sync"),
};

/**
 * A job/step name a person can recognize, in place of a raw dagster
 * identifier. Falls back to the identifier itself when it is not one of this
 * project's own jobs/steps -- callers that need the raw name alongside the
 * label (to correlate with Dagster's own UI) compose it themselves; this
 * stays label-only so it also fits a compact badge.
 */
export function jobLabel(name: string): string {
  return JOB_LABELS[name] ?? STEP_LABELS[name] ?? name;
}

type GraphqlResponse = {
  data?: {
    repositoryOrError?: { __typename: string; name?: string; location?: { name: string }; schedules?: Array<{ name: string; cronSchedule: string | null; pipelineName: string; scheduleState: { status: string } }>; jobs?: Array<{ name: string }>; assetNodes?: Array<{ assetKey: { path: string[] } }>; message?: string };
    runsOrError?: { __typename: string; results?: Array<{ runId: string; status: string; jobName: string; startTime: number | null; endTime: number | null; tags?: Array<{ key: string; value: string }> }>; message?: string };
  };
  errors?: Array<{ message?: string }>;
};

type RunEvent =
  | { __typename: "RunFailureEvent"; message: string }
  | { __typename: "ExecutionStepFailureEvent"; stepKey: string | null; message: string }
  | { __typename: string };

type RunEventsResponse = {
  data?: {
    runsOrError?: { __typename: string; results?: Array<{ eventConnection?: { events: RunEvent[]; cursor?: string; hasMore?: boolean } }> };
  };
};

/**
 * POST one named operation to the proxy. The proxy owns the query text and
 * scopes it to this project's code location (lib/dagster-scope.ts), so the
 * browser never sends a GraphQL document of its own.
 */
function postDagsterOperation(operationName: DagsterOperationName, variables: Record<string, unknown>): Promise<Response> {
  const request: DagsterOperationRequest = { operationName, variables };
  return fetch("/api/dagster/graphql", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(request),
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
}

/**
 * The run/schedule list says a run failed; it does not say why. The actual
 * reason lives in that run's own event log, one GraphQL round trip away, and
 * an operator should not have to open the separate Dagster UI just to read
 * one line of exception text. Best-effort: a run whose event log this cannot
 * fetch still shows its FAILURE badge, just without the detail line.
 */
async function fetchRunFailureMessage(runId: string): Promise<string | null> {
  try {
    let cursor: string | null = null;
    let lastStepFailure: Extract<RunEvent, { __typename: "ExecutionStepFailureEvent" }> | undefined;
    const deadline = Date.now() + 20_000;
    // 한 page만 메모리에 두고 요청 수도 제한한다. 상세 조회 실패는 목록을 막지 않는다.
    for (let page = 0; page < 20 && Date.now() < deadline; page += 1) {
      const response = await postDagsterOperation("WeatherDagsterRunFailure", { runId, cursor });
      if (!response.ok) return null;
      const payload = (await response.json()) as RunEventsResponse;
      const connection = payload.data?.runsOrError?.results?.[0]?.eventConnection;
      const events = connection?.events ?? [];
      const runFailure = events.find(
        (event): event is Extract<RunEvent, { __typename: "RunFailureEvent" }> =>
          event.__typename === "RunFailureEvent",
      );
      if (runFailure) return runFailure.message;
      // No run-level message (e.g. still CANCELING when read): fall back to the
      // last step that actually raised, which is more useful than nothing.
      const stepFailures = events.filter(
        (event): event is Extract<RunEvent, { __typename: "ExecutionStepFailureEvent" }> =>
          event.__typename === "ExecutionStepFailureEvent",
      );
      lastStepFailure = stepFailures.at(-1) ?? lastStepFailure;
      if (!connection?.hasMore || !connection.cursor || connection.cursor === cursor) break;
      cursor = connection.cursor;
    }
    if (!lastStepFailure) return null;
    return lastStepFailure.stepKey
      ? `${jobLabel(lastStepFailure.stepKey)}: ${lastStepFailure.message}`
      : lastStepFailure.message;
  } catch {
    return null;
  }
}

export async function getDagsterSnapshot(limit = 12): Promise<DagsterSnapshot> {
  const response = await postDagsterOperation("WeatherDagsterOverview", { limit });
  // A cross-origin POST is refused by the middleware with a plain-text 403, and
  // the Dagster gateway answers 502/504 with HTML. Parsing before checking the
  // status reported those as "unexpected token '교' ... is not valid json",
  // which tells a reader nothing and hides a message that was already clear.
  const body = await readBody<GraphqlResponse>(response);
  if (!response.ok) throw new Error(failureMessage(response, body, "Dagster 연결 실패"));
  const payload = body.data;
  if (!payload) throw new Error(`Dagster 응답을 해석하지 못했습니다 (${response.status})`);
  if (payload.errors?.length) throw new Error(payload.errors[0].message);
  // One repository, selected by this project's own code location: on a
  // webserver shared with other projects, theirs never reach this page.
  const repository = payload.data?.repositoryOrError;
  if (!repository || repository.__typename !== "Repository") throw new Error(repository?.message ?? "Dagster 작업 목록을 읽지 못했습니다.");
  // A PythonError or InvalidPipelineRunsFilterError has no `results`; reading
  // it as an empty list would show a broken run query as "no runs yet".
  const runs = payload.data?.runsOrError;
  if (!runs || runs.__typename !== "Runs") throw new Error(runs?.message ?? "Dagster 실행 기록을 읽지 못했습니다.");
  const results = runs.results ?? [];
  const failureMessages = new Map<string, string | null>(
    await Promise.all(
      results
        .filter((run) => run.status === "FAILURE")
        .map(async (run) => [run.runId, await fetchRunFailureMessage(run.runId)] as const),
    ),
  );
  return {
    checkedAt: new Date().toISOString(),
    repositories: [
      {
        name: repository.name ?? "",
        locationName: repository.location?.name ?? "",
        schedules: (repository.schedules ?? []).map((schedule) => ({ name: schedule.name, status: schedule.scheduleState.status, cron: schedule.cronSchedule, jobName: schedule.pipelineName })),
        jobs: (repository.jobs ?? []).map((job) => job.name),
        assets: (repository.assetNodes ?? []).map((asset) => asset.assetKey.path.join("/")),
      },
    ],
    runs: results.map((run) => ({
      runId: run.runId,
      status: run.status,
      jobName: run.jobName,
      startTime: run.startTime,
      endTime: run.endTime,
      errorMessage: failureMessages.get(run.runId) ?? null,
      ...(runtimeLimit(run.tags) !== undefined ? { maxRuntimeSeconds: runtimeLimit(run.tags) } : {}),
    })),
  };
}

function runtimeLimit(tags?: Array<{ key: string; value: string }>): number | undefined {
  const value = Number(tags?.find(tag => tag.key === "dagster/max_runtime")?.value);
  return Number.isFinite(value) && value > 0 ? value : undefined;
}
