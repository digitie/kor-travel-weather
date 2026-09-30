import { describe, expect, it } from "vitest";

import {
  DAGSTER_LOCATION_NAME,
  DAGSTER_OPERATIONS,
  DAGSTER_REPOSITORY_TAG_KEY,
  DAGSTER_REPOSITORY_TAG_VALUE,
  dagsterLocationUrl,
  dagsterRunUrl,
  dagsterScheduleUrl,
  scopedDagsterRequest,
} from "./dagster-scope";

const RUN_ID = "03147536-8f5c-41ec-a725-be2ca3b024f3";

function forwarded(raw: unknown): { operationName: string; query: string; variables: Record<string, unknown> } {
  const result = scopedDagsterRequest(raw);
  if (!result.ok) throw new Error(`refused: ${result.message}`);
  return JSON.parse(result.body);
}

function refused(raw: unknown): string {
  const result = scopedDagsterRequest(raw);
  if (result.ok) throw new Error(`forwarded: ${result.body}`);
  return result.message;
}

describe("every Dagster operation is scoped to this code location", () => {
  const operations = Object.entries(DAGSTER_OPERATIONS);

  it("has operations to check", () => {
    // Guards the loops below from passing on an empty table.
    expect(operations.length).toBeGreaterThanOrEqual(2);
  });

  it.each(operations)("%s lists no other tenant's repositories", (_name, operation) => {
    // repositoriesOrError / workspaceOrError enumerate every location a shared
    // webserver serves; runOrError(runId) reaches any tenant's run by id.
    expect(operation.query).not.toMatch(/\brepositoriesOrError\b/);
    expect(operation.query).not.toMatch(/\bworkspaceOrError\b/);
    expect(operation.query).not.toMatch(/\brunOrError\b/);
    expect(operation.query).not.toMatch(/^\s*mutation\b/m);
  });

  it.each(operations)("%s filters every run query by this location's repository tag", (_name, operation) => {
    const runQueries = operation.query.match(/runsOrError\([^)]*\)/g) ?? [];
    for (const call of runQueries) {
      expect(call).toContain(`key: "${DAGSTER_REPOSITORY_TAG_KEY}", value: $repositoryTag`);
    }
  });

  it.each(operations)("%s selects repositories only through this location's selector", (_name, operation) => {
    const repositoryQueries = operation.query.match(/repositoryOrError\([^)]*\)/g) ?? [];
    for (const call of repositoryQueries) {
      expect(call).toContain("repositoryLocationName: $repositoryLocationName");
    }
  });

  it("overview fills in the selector and the run filter server-side", () => {
    const body = forwarded({ operationName: "WeatherDagsterOverview", variables: { limit: 12 } });
    expect(body.query).toMatch(/repositoryOrError\(repositorySelector:/);
    expect(body.variables).toEqual({
      limit: 12,
      repositoryLocationName: DAGSTER_LOCATION_NAME,
      repositoryName: "__repository__",
      repositoryTag: `__repository__@${DAGSTER_LOCATION_NAME}`,
    });
  });

  it("the run-failure lookup is filtered by the same tag, not fetched by id alone", () => {
    const body = forwarded({ operationName: "WeatherDagsterRunFailure", variables: { runId: RUN_ID } });
    expect(body.query).toMatch(/runsOrError\(limit: 1, filter: \{ runIds: \[\$runId\], tags:/);
    expect(body.variables).toEqual({ runId: RUN_ID, repositoryTag: DAGSTER_REPOSITORY_TAG_VALUE });
  });
});

describe("the proxy forwards nothing else", () => {
  it("refuses a raw GraphQL document", () => {
    expect(refused({ query: "{ repositoriesOrError { __typename } }" })).toContain("query");
  });

  it("refuses a raw document even alongside a known operation name", () => {
    expect(
      refused({ operationName: "WeatherDagsterOverview", query: "mutation { terminateRun(runId: \"x\") { __typename } }", variables: { limit: 1 } }),
    ).toContain("query");
  });

  it("refuses an unknown operation", () => {
    refused({ operationName: "LaunchRun", variables: {} });
    refused({ operationName: "toString", variables: {} });
  });

  it("refuses a caller that tries to choose the scope itself", () => {
    expect(
      refused({ operationName: "WeatherDagsterOverview", variables: { limit: 12, repositoryLocationName: "kortravelmap.dagster.definitions" } }),
    ).toContain("repositoryLocationName");
    expect(
      refused({ operationName: "WeatherDagsterRunFailure", variables: { runId: RUN_ID, repositoryTag: "__repository__@other" } }),
    ).toContain("repositoryTag");
  });

  it.each(["constructor", "toString", "hasOwnProperty", "__proto__"])(
    "refuses a variable named after an Object.prototype member: %s",
    (name) => {
      // Parsed from the wire, as the proxy does: JSON.parse makes `__proto__`
      // an own key rather than setting the prototype.
      const raw = JSON.parse(`{"operationName":"WeatherDagsterOverview","variables":{"limit":12,"${name}":1}}`);
      expect(refused(raw)).toContain(name);
    },
  );

  it("refuses malformed or missing variables", () => {
    refused({ operationName: "WeatherDagsterOverview", variables: { limit: 0 } });
    refused({ operationName: "WeatherDagsterOverview", variables: { limit: 5000 } });
    refused({ operationName: "WeatherDagsterOverview", variables: { limit: "12" } });
    refused({ operationName: "WeatherDagsterOverview", variables: {} });
    refused({ operationName: "WeatherDagsterRunFailure", variables: { runId: "x\" } } mutation {" } });
    refused({ operationName: "WeatherDagsterRunFailure", variables: [RUN_ID] });
    refused(null);
    refused("WeatherDagsterOverview");
  });
});

describe("Dagster UI links", () => {
  it("open this location's own pages rather than the webserver root", () => {
    expect(dagsterLocationUrl()).toMatch(/\/locations\/kortravelweather_dagster\.definitions$/);
    expect(dagsterScheduleUrl("hourly_airkorea_weather")).toMatch(
      /\/locations\/kortravelweather_dagster\.definitions\/schedules\/hourly_airkorea_weather$/,
    );
  });

  it("keep run links global, since run ids are unique per instance", () => {
    expect(dagsterRunUrl(RUN_ID)).toMatch(new RegExp(`[^/]/runs/${RUN_ID}$`));
    expect(dagsterRunUrl(RUN_ID)).not.toContain("/locations/");
  });
});
