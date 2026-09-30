/**
 * Every Dagster GraphQL request this admin sends, and the one place that
 * scopes them to this project's own code location.
 *
 * Today Dagster serves only this project, so "every repository" and "every
 * run" happen to be ours. On a Dagster webserver shared between projects they
 * are not: an unscoped `repositoriesOrError` lists every tenant's schedules,
 * and an unscoped `runsOrError` mixes their runs into this page. So no request
 * leaves here without naming the location, and the server-side proxy forwards
 * only the operations below -- with the scope filled in by the server, not by
 * the browser -- instead of passing arbitrary GraphQL (mutations included) on
 * to a webserver other projects depend on.
 *
 * Both forms are backward compatible: a repository selector and a
 * `.dagster/repository` run-tag filter return exactly what the unscoped
 * queries returned while this project has a webserver of its own, because
 * Dagster stamps that tag on every run at creation.
 *
 * This module is imported by the browser code (lib/dagster.ts) and the proxy
 * route alike, so it must not reach for server-only APIs.
 */

/**
 * The code location name. Written first in deploy/workspace.yaml and in the
 * code-server's `-m` module; tests/test_dagster_location_scope.py fails if
 * this copy drifts from either.
 */
export const DAGSTER_LOCATION_NAME = "kortravelweather_dagster.definitions";
/** Name Dagster gives the single implicit repository of a `Definitions` module. */
export const DAGSTER_REPOSITORY_NAME = "__repository__";
/** Run tag Dagster sets on every run: `<repository>@<location>`. */
export const DAGSTER_REPOSITORY_TAG_KEY = ".dagster/repository";
export const DAGSTER_REPOSITORY_TAG_VALUE = `${DAGSTER_REPOSITORY_NAME}@${DAGSTER_LOCATION_NAME}`;

/** Variables the proxy sets itself; a request that tries to supply them is refused. */
const SCOPE_VARIABLES = {
  repositoryLocationName: DAGSTER_LOCATION_NAME,
  repositoryName: DAGSTER_REPOSITORY_NAME,
  repositoryTag: DAGSTER_REPOSITORY_TAG_VALUE,
} as const;

const RUN_TAG_FILTER = `tags: [{ key: "${DAGSTER_REPOSITORY_TAG_KEY}", value: $repositoryTag }]`;

const OVERVIEW_QUERY = `query WeatherDagsterOverview($limit: Int!, $repositoryLocationName: String!, $repositoryName: String!, $repositoryTag: String!) {
  repositoryOrError(repositorySelector: { repositoryLocationName: $repositoryLocationName, repositoryName: $repositoryName }) {
    __typename
    ... on Repository {
      name
      location { name }
      schedules { name cronSchedule pipelineName scheduleState { status } }
      jobs { name }
      assetNodes { assetKey { path } }
    }
    ... on RepositoryNotFoundError { message }
    ... on PythonError { message }
  }
  runsOrError(limit: $limit, filter: { ${RUN_TAG_FILTER} }) {
    __typename
    ... on Runs { results { runId status jobName startTime endTime } }
    ... on InvalidPipelineRunsFilterError { message }
    ... on PythonError { message }
  }
}`;

// Looked up through the same tag filter as the run list rather than
// `runOrError(runId)`: a run id alone would reach any tenant's run.
const RUN_FAILURE_QUERY = `query WeatherDagsterRunFailure($runId: String!, $repositoryTag: String!) {
  runsOrError(limit: 1, filter: { runIds: [$runId], ${RUN_TAG_FILTER} }) {
    __typename
    ... on Runs {
      results {
        eventConnection(limit: 2000) {
          events {
            __typename
            ... on RunFailureEvent { message }
            ... on ExecutionStepFailureEvent { stepKey message }
          }
        }
      }
    }
  }
}`;

const MAX_RUN_LIMIT = 100;
const RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type VariableCheck = (value: unknown) => boolean;

type DagsterOperation = {
  query: string;
  /** The only variables a caller may supply, each with its check. */
  callerVariables: Record<string, VariableCheck>;
};

export const DAGSTER_OPERATIONS = {
  WeatherDagsterOverview: {
    query: OVERVIEW_QUERY,
    callerVariables: {
      limit: (value) => Number.isInteger(value) && (value as number) >= 1 && (value as number) <= MAX_RUN_LIMIT,
    },
  },
  WeatherDagsterRunFailure: {
    query: RUN_FAILURE_QUERY,
    callerVariables: {
      runId: (value) => typeof value === "string" && RUN_ID_PATTERN.test(value),
    },
  },
} satisfies Record<string, DagsterOperation>;

export type DagsterOperationName = keyof typeof DAGSTER_OPERATIONS;

/** What the browser posts to /api/dagster/graphql: a name, never a query document. */
export type DagsterOperationRequest = {
  operationName: DagsterOperationName;
  variables: Record<string, unknown>;
};

export type ScopedDagsterRequest =
  | { ok: true; body: string }
  | { ok: false; message: string };

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Turn a browser request into the GraphQL body the webserver receives, or say
 * why it is refused. The query text and the scope variables always come from
 * this module; the caller contributes only an operation name and the
 * variables that operation declares.
 */
export function scopedDagsterRequest(raw: unknown): ScopedDagsterRequest {
  if (!isPlainObject(raw)) return { ok: false, message: "Dagster 요청 형식이 올바르지 않습니다." };
  const unexpectedKeys = Object.keys(raw).filter((key) => key !== "operationName" && key !== "variables");
  if (unexpectedKeys.length) {
    return { ok: false, message: `허용되지 않은 Dagster 요청 필드입니다: ${unexpectedKeys.join(", ")}` };
  }
  const { operationName } = raw;
  if (typeof operationName !== "string" || !Object.hasOwn(DAGSTER_OPERATIONS, operationName)) {
    return { ok: false, message: "허용되지 않은 Dagster 작업입니다." };
  }
  const operation: DagsterOperation = DAGSTER_OPERATIONS[operationName as DagsterOperationName];
  const variables = raw.variables ?? {};
  if (!isPlainObject(variables)) return { ok: false, message: "Dagster 요청 변수 형식이 올바르지 않습니다." };
  for (const [name, value] of Object.entries(variables)) {
    const check = operation.callerVariables[name];
    if (!check) return { ok: false, message: `허용되지 않은 Dagster 요청 변수입니다: ${name}` };
    if (!check(value)) return { ok: false, message: `Dagster 요청 변수 값이 올바르지 않습니다: ${name}` };
  }
  for (const name of Object.keys(operation.callerVariables)) {
    if (!Object.hasOwn(variables, name)) return { ok: false, message: `Dagster 요청 변수가 없습니다: ${name}` };
  }
  const scope = Object.fromEntries(
    Object.entries(SCOPE_VARIABLES).filter(([name]) => operation.query.includes(`$${name}:`)),
  );
  return {
    ok: true,
    body: JSON.stringify({ operationName, query: operation.query, variables: { ...variables, ...scope } }),
  };
}

/**
 * Links into the Dagster UI. Run ids are unique across a Dagster instance, so
 * run links stay global; everything else goes through this location's own
 * pages, which a shared webserver serves under `/locations/<location>` and
 * today's webserver serves the same way.
 */
export function dagsterUiBase(): string {
  return (process.env.NEXT_PUBLIC_DAGSTER_URL ?? "https://weather-dagster.digitie.mywire.org").replace(/\/+$/, "");
}

export function dagsterLocationUrl(path = ""): string {
  return `${dagsterUiBase()}/locations/${encodeURIComponent(DAGSTER_LOCATION_NAME)}${path}`;
}

export function dagsterScheduleUrl(scheduleName: string): string {
  return dagsterLocationUrl(`/schedules/${encodeURIComponent(scheduleName)}`);
}

export function dagsterRunUrl(runId: string): string {
  return `${dagsterUiBase()}/runs/${encodeURIComponent(runId)}`;
}
