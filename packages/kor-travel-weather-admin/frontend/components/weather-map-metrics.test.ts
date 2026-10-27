import { readFileSync } from "node:fs";

import ts from "typescript";
import { describe, expect, it } from "vitest";

// 실제 카드의 selector 표현식과 마커 helper를 실행해 제공처별 필드 누락을 잡는다.
const source = readFileSync(new URL("./weather-map.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("weather-map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const helpers: string[] = [];
const cards: string[] = [];
let recentRow = "";
let forecastRow = "";
function visit(node: ts.Node) {
  if (ts.isFunctionDeclaration(node) && ["metricDisplayValue", "metricDisplayUnit", "valueFor", "markerValues", "markerTemperature"].includes(node.name?.text ?? "")) {
    helpers.push(node.getText(tree));
  }
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "valueFor") cards.push(node.getText(tree));
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "values.slice(0, 8).map") recentRow = node.arguments[0].getText(tree);
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "items.slice(0, 4).map") forecastRow = node.arguments[0].getText(tree);
  ts.forEachChild(node, visit);
}
visit(tree);
if (helpers.length < 3 || cards.length !== 4 || !recentRow || !forecastRow) throw new Error("날씨 표시 helper·행을 찾지 못했습니다.");
const javascript = ts.transpileModule(
  helpers.join("\n") + `\nreturn { markerTemperature, cards: values => [${cards.join(",")}], recentRow: ${recentRow}, forecastRow: ${forecastRow} };`,
  { compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, jsxFactory: "jsx" } },
).outputText;
type Value = { metric_key: string; value_number?: number | null; value_text?: string | null; unit?: string | null };
// 실제 JSX 행을 기존 테스트 환경에서 자식 배열로 렌더해 숫자/단위 표시의 일치를 확인한다.
const actual = new Function("jsx", "datasetLabels", javascript)((_tag: unknown, _props: unknown, ...children: unknown[]) => children, new Map()) as {
  markerTemperature(summary?: { latest: Value[] }): number | null;
  cards(values: Value[]): string[];
  recentRow(value: Value): unknown[];
  forecastRow(value: Value): unknown[];
};

describe("KMA 실황 요약 표시", () => {
  it("실제 카드가 T1H·WSD·REH·RN1을 표시하며 강수 0도 유지한다", () => {
    expect(actual.cards([
      { metric_key: "T1H", value_number: -1.3, unit: "deg_c" },
      { metric_key: "WSD", value_number: 0.6, unit: "m/s" },
      { metric_key: "REH", value_number: 97, unit: "%" },
      { metric_key: "RN1", value_number: 0, unit: "mm" },
    ])).toEqual(["-1.3 ℃", "0.6 m/s", "97 %", "0 mm"]);
  });
  it("기온과 풍속만 읽기 쉬운 단위·소수 자릿수로 표시한다", () => {
    expect(actual.cards([
      { metric_key: "TEMP", value_number: 14.4, unit: "deg_c" },
      { metric_key: "WIND_SPEED", value_number: 5.5556, unit: "m/s" },
      { metric_key: "HUMIDITY", value_number: 52, unit: "%" },
      { metric_key: "PRECIP", value_number: 0.04, unit: "mm" },
    ])).toEqual(["14.4 ℃", "5.6 m/s", "52 %", "0.04 mm"]);
  });
  it.each(["deg_c", "°C", "℃"])("%s의 음수·0을 보존하고 동일한 기온 단위를 쓴다", (unit) => {
    for (const [value_number, expected] of [[0, "0 ℃"], [-2.56, "-2.6 ℃"], [-0.04, "-0.04 ℃"]] as const) {
      expect(actual.cards([{ metric_key: "TEMP", value_number, unit }])[0]).toBe(expected);
    }
  });
  it("null·문자열·기호는 숫자0으로 강제하지 않는다", () => {
    for (const [value_text, expected] of [[null, "— ℃"], ["unknown", "unknown ℃"], ["<0.1", "<0.1 ℃"], ["−2", "−2 ℃"], ["0", "0 ℃"]] as const) {
      expect(actual.cards([{ metric_key: "TEMP", value_number: null, value_text, unit: "deg_c" }])[0]).toBe(expected);
    }
    expect(actual.cards([])).toEqual(["—", "—", "—", "—"]);
  });
  it("강수량·미지원 단위의 원래 정밀도와 작은 풍속을 보존한다", () => {
    for (const [value_number, unit, expected] of [[0.04, "mm", "0.04 mm"], [0, "mm", "0 mm"], [5.5556, "knots", "5.5556 knots"], [0.04, "m/s", "0.04 m/s"], [-5.5556, "m/s", "-5.6 m/s"], [0, "m/s", "0 m/s"]] as const) {
      expect(actual.cards([{ metric_key: "WIND_SPEED", value_number, unit }])[1]).toBe(expected);
    }
    expect(actual.cards([{ metric_key: "TEMP", value_number: 14.4444, unit: "K" }])[0]).toBe("14.4444 K");
    expect(actual.cards([{ metric_key: "TEMP", value_number: 0, unit: null }])[0]).toBe("0");
    expect(actual.cards([{ metric_key: "TEMP", value_number: null, value_text: "unknown", unit: null }])[0]).toBe("unknown");
  });
  it("최근 측정과 예보 행도 카드와 같은 단위·정밀도를 사용한다", () => {
    for (const [value, display, unit] of [
      [{ metric_key: "TEMP", value_number: 14.4, unit: "deg_c" }, "14.4", "℃"],
      [{ metric_key: "WIND_SPEED", value_number: 5.5556, unit: "m/s" }, "5.6", "m/s"],
      [{ metric_key: "PRECIP", value_number: 0.04, unit: "mm" }, "0.04", "mm"],
    ] as const) {
      for (const render of [actual.recentRow, actual.forecastRow]) {
        const text = render(value).flat(Infinity).filter((part) => typeof part === "string" || typeof part === "number").join("");
        expect(text).toContain(`${display} ${unit}`);
        expect(text).not.toContain("deg_c");
      }
    }
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
