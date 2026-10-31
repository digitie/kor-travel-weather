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
}).outputText)() as { markerState(summary: unknown): { kind: string; label: string }; markerCondition(kind: string): string; markerTemperature(summary: unknown): number | null };
const classify = actual.markerState;
const target_at = "2026-10-24T01:00:00Z";

describe("종류를 선택한 예보 마커", () => {
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
