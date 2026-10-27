import { readFileSync } from "node:fs";

import ts from "typescript";
import { describe, expect, it, vi } from "vitest";


// 실제 effect callback을 실행한다. DOM/지도 대신 React의 cleanup→새 effect 순서만 명시한다.
const source = readFileSync(new URL("./weather-map.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("weather-map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let markerMemo: ts.Expression | undefined;
function visit(node: ts.Node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(tree) === "markers") markerMemo = node.initializer;
  ts.forEachChild(node, visit);
}
visit(tree);
describe("마커 모델 참조 보존", () => {
  function renderer() {
    if (!markerMemo) throw new Error("marker 모델의 useMemo가 필요합니다.");
    const render = new Function(
      "useMemo", "mapLocations", "selectedId", "summaries", "setSelectedId",
      "markerState", "markerTemperature", "markerCondition", "markerRefreshRef", "providerFilter", "refreshToken", "kindFilter", "setDetailOpen", "requestAnimationFrame", "detailRef",
      ts.transpileModule("return " + markerMemo.getText(tree), {
        compilerOptions: { target: ts.ScriptTarget.ES2022 },
      }).outputText,
    );
    let previous: unknown[] | undefined;
    let value: unknown;
    const useMemo = (factory: () => unknown, deps: unknown[]) => {
      if (!previous || deps.some((dep, index) => !Object.is(dep, previous![index]))) {
        previous = deps;
        value = factory();
      }
      return value;
    };
    const select = vi.fn();
    const locations = [
      { location_id: "a", name: "가", longitude: 127, latitude: 37 },
      { location_id: "b", name: "나", longitude: 128, latitude: 36 },
    ];
    const call = (selectedId = "a", summaries = {}, points = locations) => render(
      useMemo, points, selectedId, summaries, select,
      () => ({ kind: "clear", label: "맑음" }),
      (summary: { temperature?: number } | undefined) => summary?.temperature ?? null,
      (kind: string) => kind, { current: JSON.stringify([0, undefined, undefined]) }, undefined, 0, undefined, vi.fn(), vi.fn(), { current: null },
    ) as Array<{
      id: string; selected: boolean; temperature: number | null;
      lngLat: number[]; title: string; onClick: () => void;
    }>;
    return { call, locations, select };
  }

  it("상세·제공처 등 무관한 렌더는 같은 배열을 유지하고 summary 변경은 반영한다", () => {
    const view = renderer();
    const summaries = {};
    const initial = view.call("a", summaries);
    expect(view.call("a", summaries)).toBe(initial);
    const updated = view.call("a", { a: { temperature: 21, alerts: [] } });
    expect(updated).not.toBe(initial);
    expect(updated[0].temperature).toBe(21);
  });

  it("선택과 viewport·좌표·이름 변경을 반영하며 클릭 대상도 보존한다", () => {
    const view = renderer();
    const summaries = {};
    const initial = view.call("a", summaries);
    const selected = view.call("b", summaries);
    expect(selected).not.toBe(initial);
    expect(selected.map((marker) => marker.selected)).toEqual([false, true]);
    selected[1].onClick();
    expect(view.select).toHaveBeenCalledWith("b");
    const moved = view.call("b", summaries, [{ ...view.locations[1], name: "새 이름", longitude: 129 }]);
    expect(moved).not.toBe(selected);
    expect(moved[0].lngLat).toEqual([129, 36]);
    expect(moved[0].title).toBe("새 이름");
  });
});

describe("조회 실패의 미확인 상태", () => {
  it("미확인 상태를 실제 구름과 구별한다", () => {
    const declaration = tree.statements.find((node) =>
      ts.isFunctionDeclaration(node) && node.name?.text === "markerCondition",
    );
    if (!declaration) throw new Error("마커 상태 변환이 필요합니다.");
    const convert = new Function(ts.transpileModule(declaration.getText(tree) + "; return markerCondition;",
      { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText)();
    expect(convert("unknown")).toBe("unknown"); expect(convert("cloud")).toBe("cloudy");
  });
});
