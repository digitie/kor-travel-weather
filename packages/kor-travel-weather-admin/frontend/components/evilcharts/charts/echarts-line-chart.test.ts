import { readFileSync } from "node:fs";
import ts from "typescript";
import { expect, it, vi } from "vitest";

const source = readFileSync(new URL("./echarts-line-chart.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("chart.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let initializer: ts.Expression | undefined;
function visit(node: ts.Node) { if (ts.isVariableDeclaration(node) && node.name.getText(tree) === "toggleSelection") initializer = node.initializer; ts.forEachChild(node, visit); }
visit(tree);
if (!initializer) throw new Error("실제 선택 callback이 필요합니다.");
const code = ts.transpileModule("return " + initializer.getText(tree), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
it("state updater 재실행과 같은 batch의 연속 선택에서도 callback은 한 번씩 최신 대상을 부른다", () => {
  const first = vi.fn(), next = vi.fn();
  const live = { hoveredKey: null, handlers: { selectedDataKey: null as string | null, onSelectionChange: first } };
  let selected: string | null = null;
  const setter = vi.fn((value: string | null | ((previous: string | null) => string | null)) => {
    if (typeof value === "function") { value(selected); selected = value(selected); } else selected = value;
  });
  const toggle = new Function("useCallback", "live", "echartsRef", "setHoveredDataKey", "setSelectedDataKey", "onSelectionChange", code)((callback: unknown) => callback, live, { current: null }, vi.fn(), setter, first);
  toggle("temperature");
  live.handlers.onSelectionChange = next;
  toggle("temperature");
  expect(first.mock.calls).toEqual([["temperature"]]); expect(next.mock.calls).toEqual([[null]]);
  expect(setter.mock.calls).toEqual([["temperature"], [null]]);
});
