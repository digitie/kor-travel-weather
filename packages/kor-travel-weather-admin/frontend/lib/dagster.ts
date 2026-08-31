export type DagsterSchedule = { name: string; status: string | null; cron: string | null };
export type DagsterRepository = { name: string; locationName: string; schedules: DagsterSchedule[]; jobs: string[]; assets: string[] };
export type DagsterRun = { runId: string; status: string; jobName: string; startTime: number | null; endTime: number | null };
export type DagsterSnapshot = { repositories: DagsterRepository[]; runs: DagsterRun[]; checkedAt: string };

type GraphqlResponse = {
  data?: {
    repositoriesOrError?: { __typename: string; nodes?: Array<{ name: string; location: { name: string }; schedules: Array<{ name: string; cronSchedule: string | null; scheduleState: { status: string } }>; jobs: Array<{ name: string }>; assetNodes: Array<{ assetKey: { path: string[] } }> }>; message?: string };
    runsOrError?: { __typename: string; results?: Array<{ runId: string; status: string; jobName: string; startTime: number | null; endTime: number | null }>; message?: string };
  };
  errors?: Array<{ message?: string }>;
};

const QUERY = `query WeatherDagsterOverview($limit: Int!) {
  repositoriesOrError {
    __typename
    ... on RepositoryConnection {
      nodes {
        name
        location { name }
        schedules { name cronSchedule scheduleState { status } }
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
  if (!repositories || !repositories.nodes) throw new Error(repositories?.message ?? "Dagster repository를 읽지 못했습니다.");
  const runs = payload.data?.runsOrError;
  return {
    checkedAt: new Date().toISOString(),
    repositories: repositories.nodes.map((repository) => ({
      name: repository.name,
      locationName: repository.location.name,
      schedules: repository.schedules.map((schedule) => ({ name: schedule.name, status: schedule.scheduleState.status, cron: schedule.cronSchedule })),
      jobs: repository.jobs.map((job) => job.name),
      assets: repository.assetNodes.map((asset) => asset.assetKey.path.join("/")),
    })),
    runs: runs?.results?.map((run) => ({ runId: run.runId, status: run.status, jobName: run.jobName, startTime: run.startTime, endTime: run.endTime })) ?? [],
  };
}
