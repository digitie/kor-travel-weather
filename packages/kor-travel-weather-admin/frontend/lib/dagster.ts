export type DagsterSchedule = { name: string; status: string | null; cron: string | null; jobName: string };
export type DagsterRepository = { name: string; locationName: string; schedules: DagsterSchedule[]; jobs: string[]; assets: string[] };
export type DagsterRun = { runId: string; status: string; jobName: string; startTime: number | null; endTime: number | null; errorMessage: string | null };
export type DagsterSnapshot = { repositories: DagsterRepository[]; runs: DagsterRun[]; checkedAt: string };

// dagster job/schedule/asset names are internal identifiers, not something an
// operator glancing at this page should have to already know by heart.
const JOB_LABELS: Record<string, string> = {
  kma_weather_job: "기상청 단기예보 수집",
  airkorea_weather_job: "에어코리아 대기질 수집",
  external_weather_job: "외부 제공 날씨 수집",
  weather_retention_job: "보존 기간 만료 데이터 정리",
  regional_weather_job: "지역별(해수욕장·산·고속도로) 날씨 수집",
};

const RUN_STATUS_LABELS: Record<string, string> = {
  SUCCESS: "성공",
  FAILURE: "실패",
  STARTED: "진행 중",
  STARTING: "시작 중",
  QUEUED: "대기 중",
  CANCELING: "취소 중",
  CANCELED: "취소됨",
  NOT_STARTED: "대기",
};

/** A Dagster run status a person can read, falling back to the raw enum for any status this project doesn't expect. */
export function runStatusLabel(status: string): string {
  return RUN_STATUS_LABELS[status] ?? status;
}

const STEP_LABELS: Record<string, string> = {
  kma_weather_sync: "기상청 단기예보 수집",
  airkorea_weather_sync: "에어코리아 대기질 수집",
  external_weather_sync: "외부 제공 날씨 수집",
  weather_retention_purge: "보존 기간 만료 데이터 정리",
  khoa_beach_index_sync: "해수욕장 지수 수집",
  krforest_mountain_sync: "산악 날씨 수집",
  krforest_dust_sync: "청정넷 미세먼지 수집",
  krex_restarea_sync: "고속도로 휴게소 날씨 수집",
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

/** Seconds a run now shown as STARTED has been running, or null if it isn't. */
export function runElapsedSeconds(run: DagsterRun, nowSeconds: number): number | null {
  if (run.status !== "STARTED" || run.startTime == null) return null;
  return Math.max(0, nowSeconds - run.startTime);
}

/**
 * A STARTED run past this age is worth an operator's attention: either it is
 * a genuinely long batch, or it is the exact zombie-run failure mode
 * documented in deploy/dagster.yaml -- a run whose process died holding its
 * concurrency slot for ever. Either way "still STARTED" alone does not say
 * which, so the page needs to call out the age instead of a bare badge.
 */
export const STALLED_RUN_THRESHOLD_SECONDS = 600;

export function isStalledRun(run: DagsterRun, nowSeconds: number): boolean {
  const elapsed = runElapsedSeconds(run, nowSeconds);
  return elapsed !== null && elapsed >= STALLED_RUN_THRESHOLD_SECONDS;
}

/** "1시간 12분" style duration, for a person -- not "4320s" or a raw epoch delta. */
export function formatElapsed(seconds: number): string {
  const totalMinutes = Math.floor(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours > 0) return `${hours}시간 ${minutes}분`;
  if (minutes > 0) return `${minutes}분`;
  return "1분 미만";
}

/**
 * A cron string is precise but not something a person reads at a glance.
 * Covers this project's own schedules (every hour, a couple of fixed times a
 * day) in plain Korean; anything shaped differently falls back to the raw
 * expression rather than guessing wrong.
 */
export function describeCron(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, day, month, weekday] = parts;
  if (day !== "*" || month !== "*" || weekday !== "*") return cron;
  if (hour === "*") {
    if (/^\d+$/.test(minute)) {
      return minute === "0" ? "매시 정각" : `매시 ${minute}분`;
    }
    return cron;
  }
  const minuteNum = Number(minute);
  if (!/^\d+$/.test(minute) || Number.isNaN(minuteNum)) return cron;
  const hours = hour.split(",");
  if (!hours.every((value) => /^\d+$/.test(value))) return cron;
  const times = hours.map((value) => `${value.padStart(2, "0")}:${minute.padStart(2, "0")}`);
  return `매일 ${times.join(", ")}`;
}

type GraphqlResponse = {
  data?: {
    repositoriesOrError?: { __typename: string; nodes?: Array<{ name: string; location: { name: string }; schedules: Array<{ name: string; cronSchedule: string | null; pipelineName: string; scheduleState: { status: string } }>; jobs: Array<{ name: string }>; assetNodes: Array<{ assetKey: { path: string[] } }> }>; message?: string };
    runsOrError?: { __typename: string; results?: Array<{ runId: string; status: string; jobName: string; startTime: number | null; endTime: number | null }>; message?: string };
  };
  errors?: Array<{ message?: string }>;
};

type RunEvent =
  | { __typename: "RunFailureEvent"; message: string }
  | { __typename: "ExecutionStepFailureEvent"; stepKey: string | null; message: string }
  | { __typename: string };

type RunEventsResponse = {
  data?: {
    runOrError?: { __typename: string; eventConnection?: { events: RunEvent[] } };
  };
};

const RUN_FAILURE_QUERY = `query WeatherDagsterRunFailure($runId: ID!) {
  runOrError(runId: $runId) {
    __typename
    ... on Run {
      eventConnection(limit: 2000) {
        events {
          __typename
          ... on RunFailureEvent { message }
          ... on ExecutionStepFailureEvent { stepKey message }
        }
      }
    }
  }
}`;

/**
 * The run/schedule list says a run failed; it does not say why. The actual
 * reason lives in that run's own event log, one GraphQL round trip away, and
 * an operator should not have to open the separate Dagster UI just to read
 * one line of exception text. Best-effort: a run whose event log this cannot
 * fetch still shows its FAILURE badge, just without the detail line.
 */
async function fetchRunFailureMessage(runId: string): Promise<string | null> {
  try {
    const response = await fetch("/api/dagster/graphql", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ query: RUN_FAILURE_QUERY, variables: { runId } }),
      cache: "no-store",
    });
    if (!response.ok) return null;
    const payload = (await response.json()) as RunEventsResponse;
    const events = payload.data?.runOrError?.eventConnection?.events ?? [];
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
    const lastStepFailure = stepFailures.at(-1);
    if (!lastStepFailure) return null;
    return lastStepFailure.stepKey
      ? `${jobLabel(lastStepFailure.stepKey)}: ${lastStepFailure.message}`
      : lastStepFailure.message;
  } catch {
    return null;
  }
}

const QUERY = `query WeatherDagsterOverview($limit: Int!) {
  repositoriesOrError {
    __typename
    ... on RepositoryConnection {
      nodes {
        name
        location { name }
        schedules { name cronSchedule pipelineName scheduleState { status } }
        jobs { name }
        assetNodes { assetKey { path } }
      }
    }
    ... on PythonError { message }
  }
  runsOrError(limit: $limit) {
    __typename
    ... on Runs { results { runId status jobName startTime endTime } }
    ... on PythonError { message }
  }
}`;

export async function getDagsterSnapshot(limit = 12): Promise<DagsterSnapshot> {
  const response = await fetch("/api/dagster/graphql", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({ query: QUERY, variables: { limit } }),
    cache: "no-store",
  });
  const payload = (await response.json()) as GraphqlResponse;
  if (!response.ok || payload.errors?.length) throw new Error(payload.errors?.[0]?.message ?? `Dagster 연결 실패 (${response.status})`);
  const repositories = payload.data?.repositoriesOrError;
  if (!repositories || !repositories.nodes) throw new Error(repositories?.message ?? "Dagster 작업 목록을 읽지 못했습니다.");
  const runs = payload.data?.runsOrError;
  const results = runs?.results ?? [];
  const failureMessages = new Map<string, string | null>(
    await Promise.all(
      results
        .filter((run) => run.status === "FAILURE")
        .map(async (run) => [run.runId, await fetchRunFailureMessage(run.runId)] as const),
    ),
  );
  return {
    checkedAt: new Date().toISOString(),
    repositories: repositories.nodes.map((repository) => ({
      name: repository.name,
      locationName: repository.location.name,
      schedules: repository.schedules.map((schedule) => ({ name: schedule.name, status: schedule.scheduleState.status, cron: schedule.cronSchedule, jobName: schedule.pipelineName })),
      jobs: repository.jobs.map((job) => job.name),
      assets: repository.assetNodes.map((asset) => asset.assetKey.path.join("/")),
    })),
    runs: results.map((run) => ({
      runId: run.runId,
      status: run.status,
      jobName: run.jobName,
      startTime: run.startTime,
      endTime: run.endTime,
      errorMessage: failureMessages.get(run.runId) ?? null,
    })),
  };
}
