import { readFileSync } from "node:fs";
import ts from "typescript";
import { describe, expect, it } from "vitest";

// 현재값과 예보 배열을 분리해서 받은 실제 마커 분류 경로를 실행한다.
const source = readFileSync(new URL("./weather-map.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("weather-map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const helpers: string[] = [];
for (const node of tree.statements) {
  if (ts.isFunctionDeclaration(node) && ["numericCode", "classifyProviderCode", "markerState"].includes(node.name?.text ?? "")) helpers.push(node.getText(tree));
}
const classify = new Function(ts.transpileModule(helpers.join("\n") + "\nreturn markerState;", {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText)() as (summary: unknown) => { kind: string; label: string };

describe("종류를 선택한 예보 마커", () => {
  it.each([["맑음", "clear"], ["구름많음", "cloud"], ["흐리고 비", "rain"], ["흐리고 눈", "snow"]])("중기 SKY %s를 표시한다", (value_text, kind) => {
    expect(classify({ latest: [], alerts: [], forecast: [{ provider: "python-kma-api", metric_key: "SKY", value_number: null, value_text }] }).kind).toBe(kind);
  });
  it("특보가 있으면 예보 상태와 구분해 우선 표시한다", () => {
    expect(classify({ latest: [], forecast: [], alerts: [{}] }).label).toBe("특보 1건");
  });
  it("SKY/PTY 행 순서에 관계없이 강수를 우선하며 PTY 0을 맑음으로 단정하지 않는다", () => {
    const sky = { provider: "python-kma-api", metric_key: "SKY", value_number: 1 };
    const rain = { provider: "python-kma-api", metric_key: "PTY", value_number: 1 };
    for (const forecast of [[sky, rain], [rain, sky]]) expect(classify({ latest: [], alerts: [], forecast }).kind).toBe("rain");
    expect(classify({ latest: [], alerts: [], forecast: [{ ...rain, value_number: 0 }, { ...sky, value_number: 3 }] }).kind).toBe("cloud");
  });
});
