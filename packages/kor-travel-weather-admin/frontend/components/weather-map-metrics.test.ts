import { readFileSync } from "node:fs";

import ts from "typescript";
import { describe, expect, it } from "vitest";

// 실제 카드의 selector 표현식과 마커 helper를 실행해 제공처별 필드 누락을 잡는다.
const source = readFileSync(new URL("./weather-map.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("weather-map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const helpers: string[] = [];
const cards: string[] = [];
function visit(node: ts.Node) {
  if (ts.isFunctionDeclaration(node) && ["valueFor", "markerTemperature"].includes(node.name?.text ?? "")) {
    helpers.push(node.getText(tree));
  }
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "valueFor") cards.push(node.getText(tree));
  ts.forEachChild(node, visit);
}
visit(tree);
if (helpers.length !== 2 || cards.length !== 4) throw new Error("날씨 카드 helper를 찾지 못했습니다.");
const javascript = ts.transpileModule(
  helpers.join("\n") + `\nreturn { markerTemperature, cards: values => [${cards.join(",")}] };`,
  { compilerOptions: { target: ts.ScriptTarget.ES2022 } },
).outputText;
type Value = { metric_key: string; value_number?: number | null; value_text?: string | null; unit?: string };
const actual = new Function(javascript)() as {
  markerTemperature(summary?: { latest: Value[] }): number | null;
  cards(values: Value[]): string[];
};

describe("KMA 실황 요약 표시", () => {
  it("실제 카드가 T1H·WSD·REH·RN1을 표시하며 강수 0도 유지한다", () => {
    expect(actual.cards([
      { metric_key: "T1H", value_number: -1.3, unit: "deg_c" },
      { metric_key: "WSD", value_number: 0.6, unit: "m/s" },
      { metric_key: "REH", value_number: 97, unit: "%" },
      { metric_key: "RN1", value_number: 0, unit: "mm" },
    ])).toEqual(["-1.3 deg_c", "0.6 m/s", "97 %", "0 mm"]);
  });
  it("T1H 마커의 음수·숫자 0·문자열 0을 표시한다", () => {
    for (const [value, expected] of [[{ value_number: -1.3 }, -1.3], [{ value_number: 0 }, 0], [{ value_text: "0" }, 0]] as const) {
      expect(actual.markerTemperature({ latest: [{ metric_key: "T1H", ...value }] })).toBe(expected);
    }
  });
  it("결측·공백·숫자가 아닌 값은 0도로 만들지 않는다", () => {
    for (const value_text of [null, undefined, "", " ", "missing"]) {
      expect(actual.markerTemperature({ latest: [{ metric_key: "T1H", value_number: null, value_text }] })).toBeNull();
    }
    expect(actual.markerTemperature()).toBeNull();
  });
});
