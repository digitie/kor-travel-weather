import { readFileSync } from "node:fs";
import ts from "typescript";
import { describe, expect, it } from "vitest";

// 현재값과 예보 배열을 분리해서 받은 실제 마커 분류 경로를 실행한다.
const source = readFileSync(new URL("./weather-map.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("weather-map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const helpers: string[] = [];
for (const node of tree.statements) {
  if (ts.isFunctionDeclaration(node) && ["numericCode", "classifyProviderCode", "markerValues", "markerState", "markerCondition", "markerTemperature"].includes(node.name?.text ?? "")) helpers.push(node.getText(tree));
}
const actual = new Function(ts.transpileModule(helpers.join("\n") + "\nreturn { markerState, markerCondition, markerTemperature };", {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText)() as { markerState(summary: unknown, referenceTime?: number): { kind: string; label: string }; markerCondition(kind: string): string; markerTemperature(summary: unknown): number | null };
const classify = actual.markerState;
const target_at = "2026-10-24T01:00:00Z";

describe("종류를 선택한 예보 마커", () => {
  const now = Date.parse("2026-11-16T08:42:00Z");
  const currentSky = { provider: "python-kma-api", metric_key: "SKY", value_number: 3, target_at: "2026-11-16T08:00:00Z" };
  const nowcast = [
    { provider: "python-kma-api", metric_key: "T1H", value_number: 19.2, target_at: "2026-11-16T05:00:00Z" },
    { provider: "python-kma-api", metric_key: "PTY", value_number: 0, target_at: "2026-11-16T05:00:00Z" },
  ];
  it.each([[1, "clear"], [3, "cloud"], [4, "cloud"]])("실황에 하늘 상태가 없으면 현재 시간 예보 SKY %s를 보완한다", (value_number, kind) => {
    const summary = { latest: nowcast, alerts: [], forecast: [{ ...currentSky, value_number }] };
    expect(actual.markerState(summary, now)).toMatchObject({ kind, label: expect.stringContaining("예보") });
    expect(actual.markerTemperature(summary)).toBe(19.2);
  });
  it("실황 강수와 하늘 상태는 예보로 덮지 않는다", () => {
    const forecast = [{ ...currentSky, value_number: 1 }];
    expect(actual.markerState({ latest: [{ ...nowcast[1], value_number: 1 }], alerts: [], forecast }, now).kind).toBe("rain");
    expect(actual.markerState({ latest: [{ ...currentSky, value_number: 4 }], alerts: [], forecast }, now).kind).toBe("cloud");
  });
  it.each(["2026-11-16T09:00:00Z", "2026-11-16T07:00:00Z", "invalid"])("현재 시간대 밖의 SKY %s는 실황 보완에 쓰지 않는다", target_at => {
    expect(actual.markerState({ latest: nowcast, alerts: [], forecast: [{ ...currentSky, target_at }] }, now).kind).toBe("unknown");
  });
  it("예보 강수와 기온은 실황 보완에 섞지 않고 하늘 상태 미제공은 유지한다", () => {
    const forecast = [
      { ...currentSky, metric_key: "PTY", value_number: 1 },
      { ...currentSky, metric_key: "TMP", value_number: 8 },
    ];
    const summary = { latest: nowcast, alerts: [], forecast };
    expect(actual.markerState(summary, now).kind).toBe("unknown");
    expect(actual.markerTemperature(summary)).toBe(19.2);
    expect(actual.markerState({ latest: nowcast, alerts: [], forecast: [{ ...currentSky, value_number: null, value_text: "흐리고 비" }] }, now).kind).toBe("unknown");
  });
  it.each([["맑음", "clear"], ["구름많음", "cloud"], ["흐리고 비", "rain"], ["흐리고 눈", "snow"]])("중기 SKY %s를 표시한다", (value_text, kind) => {
    expect(classify({ latest: [], alerts: [], forecast: [{ provider: "python-kma-api", metric_key: "SKY", value_number: null, value_text, target_at }] }).kind).toBe(kind);
  });
  it.each([[1, "clear", "sunny"], [3, "cloud", "cloudy"]])("풍랑특보가 있어도 SKY %s의 실제 날씨를 유지한다", (code, kind, condition) => {
    const summary = { latest: [{ provider: "python-kma-api", metric_key: "SKY", value_number: code }], forecast: [], alerts: [{ title: "풍랑주의보" }] };
    const state = classify(summary);
    expect(state.kind).toBe(kind);
    expect(actual.markerCondition(state.kind)).toBe(condition);
  });
  it("특보만 있고 날씨가 없으면 뇌우로 만들지 않는다", () => {
    const state = classify({ latest: [], forecast: [], alerts: [{ title: "풍랑주의보" }] });
    expect(state.kind).toBe("unknown");
    expect(actual.markerCondition(state.kind)).toBe("unknown");
  });
  it("실제 뇌우 코드가 있으면 특보 유무와 관계없이 뇌우를 표시한다", () => {
    for (const alerts of [[], [{ title: "풍랑주의보" }]]) {
      const state = classify({ latest: [{ provider: "open_meteo", metric_key: "WEATHER_CODE", value_number: 95 }], forecast: [], alerts });
      expect(state.kind).toBe("storm");
      expect(actual.markerCondition(state.kind)).toBe("storm");
    }
  });
  it("SKY/PTY 행 순서에 관계없이 강수를 우선하며 PTY 0을 맑음으로 단정하지 않는다", () => {
    const sky = { provider: "python-kma-api", metric_key: "SKY", value_number: 1, target_at };
    const rain = { provider: "python-kma-api", metric_key: "PTY", value_number: 1, target_at };
    for (const forecast of [[sky, rain], [rain, sky]]) expect(classify({ latest: [], alerts: [], forecast }).kind).toBe("rain");
    expect(classify({ latest: [], alerts: [], forecast: [{ ...rain, value_number: 0 }, { ...sky, value_number: 3 }] }).kind).toBe("cloud");
  });
  it("현재 맑음·20도에 미래 비·10도를 섞지 않는다", () => {
    const summary = { alerts: [], latest: [
      { provider: "python-kma-api", metric_key: "SKY", value_number: 1 },
      { provider: "python-kma-api", metric_key: "PTY", value_number: 0 },
      { metric_key: "T1H", value_number: 20 },
    ], forecast: [
      { provider: "python-kma-api", metric_key: "PTY", value_number: 1, target_at },
      { metric_key: "TMP", value_number: 10, target_at },
    ] };
    expect(classify(summary).kind).toBe("clear");
    expect(actual.markerTemperature(summary)).toBe(20);
  });
  it("예보만 선택하면 가장 이른 같은 대상 시각의 상태와 온도를 표시한다", () => {
    const summary = { alerts: [], latest: [], forecast: [
      { provider: "python-kma-api", metric_key: "PTY", value_number: 1, target_at: "2026-10-24T02:00:00Z" },
      { metric_key: "TMP", value_number: 5, target_at: "2026-10-24T02:00:00Z" },
      { provider: "python-kma-api", metric_key: "SKY", value_number: 1, target_at },
      { metric_key: "TMP", value_number: 10, target_at },
    ] };
    expect(classify(summary).kind).toBe("clear");
    expect(actual.markerTemperature(summary)).toBe(10);
    summary.forecast = summary.forecast.filter((row) => !(row.metric_key === "TMP" && row.target_at === target_at));
    expect(actual.markerTemperature(summary)).toBeNull();
  });
});
