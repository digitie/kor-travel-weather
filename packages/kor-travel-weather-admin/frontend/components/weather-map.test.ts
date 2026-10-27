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

function declaration(name: string) {
  const node = tree.statements.find((statement) => (ts.isFunctionDeclaration(statement) && statement.name?.text === name)
    || (ts.isVariableStatement(statement) && statement.declarationList.declarations.some((item) => item.name.getText(tree) === name)));
  if (!node) throw new Error(`${name} 선언을 찾지 못했습니다.`);
  return node.getText(tree);
}

// 의존성 배열 문자열로 실제 effect를 찾아 이름 붙은 인자로 실행한다.
function effectWith(deps: string, names: string[], prelude: string[] = []) {
  let found: ts.ArrowFunction | undefined;
  function find(node: ts.Node) {
    if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect"
      && node.arguments[1]?.getText(tree) === deps) found = node.arguments[0] as ts.ArrowFunction;
    ts.forEachChild(node, find);
  }
  find(tree);
  if (!found) throw new Error(`${deps} effect를 찾지 못했습니다.`);
  const helpers = prelude.map(declaration);
  return new Function(...names, ts.transpileModule(
    helpers.join("\n") + "\nreturn (" + found.getText(tree) + ")();",
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } },
  ).outputText) as (...args: unknown[]) => (() => void) | undefined;
}

describe("필터 변경 중 선택·상세·카메라 보존", () => {
  const reconcile = effectWith("[catalogLoading, selectedId, sourceLoading, visibleLocations]", [
    "catalogLoading", "sourceLoading", "visibleLocations", "selectedId", "setSelectedId",
    "setDetailOpen", "automaticSelectionRef",
  ]);
  function run(visibleLocations: { location_id: string }[], selectedId: string, sourceLoading = false) {
    const view = { setSelectedId: vi.fn(), setDetailOpen: vi.fn(), automaticSelectionRef: { current: null as string | null } };
    reconcile(false, sourceLoading, visibleLocations, selectedId, view.setSelectedId, view.setDetailOpen, view.automaticSelectionRef);
    return view;
  }

  it("선택 조건의 위치를 불러오는 동안의 빈 목록은 선택과 상세를 지우지 않는다", () => {
    const loading = run([], "a", true);
    expect(loading.setSelectedId).not.toHaveBeenCalled();
    expect(loading.setDetailOpen).not.toHaveBeenCalled();
  });

  it("아직 보이는 선택은 유지하고, 빠진 선택만 대체하며 상세를 닫는다", () => {
    const kept = run([{ location_id: "b" }, { location_id: "a" }], "a");
    expect(kept.setSelectedId).not.toHaveBeenCalled();
    expect(kept.setDetailOpen).not.toHaveBeenCalled();
    const replaced = run([{ location_id: "b" }], "a");
    expect(replaced.setSelectedId).toHaveBeenCalledWith("b");
    expect(replaced.setDetailOpen).toHaveBeenCalledWith(false);
    expect(replaced.automaticSelectionRef.current).toBe("b");
    const initial = run([{ location_id: "b" }], "");
    expect(initial.setSelectedId).toHaveBeenCalledWith("b");
    expect(initial.automaticSelectionRef.current).toBeNull();
    const empty = run([], "a");
    expect(empty.setSelectedId).toHaveBeenCalledWith("");
    expect(empty.setDetailOpen).toHaveBeenCalledWith(false);
  });

  it("같은 위치의 새 객체와 자동 대체 선택은 카메라를 옮기지 않고 명시 선택만 옮긴다", () => {
    const camera = effectWith("[mapReady, selectedLocationId]", [
      "automaticSelectionRef", "selectedLocationId", "selectedPositionRef", "map", "mapReady",
      "zoomOnFirstMoveRef",
    ], ["SELECTED_ZOOM"]);
    const easeTo = vi.fn();
    const map = { current: { easeTo } };
    const automaticSelectionRef = { current: null as string | null };
    const zoomOnFirstMoveRef = { current: false };
    const selectedPositionRef = { current: null as [number, number] | null };
    let previous: unknown[] | undefined;
    // React처럼 의존성이 같으면 effect를 다시 실행하지 않는다. 위치 ref는 매 commit 갱신한다.
    const commit = (id: string, position: [number, number]) => {
      selectedPositionRef.current = position;
      const deps = [true, id];
      if (previous && deps.every((dep, index) => Object.is(dep, previous![index]))) return;
      previous = deps;
      camera(automaticSelectionRef, id, selectedPositionRef, map, true, zoomOnFirstMoveRef);
    };
    commit("a", [127, 37]);
    expect(easeTo).toHaveBeenCalledTimes(1);
    commit("a", [127, 37]);
    expect(easeTo).toHaveBeenCalledTimes(1);
    automaticSelectionRef.current = "b";
    commit("b", [128, 36]);
    expect(easeTo).toHaveBeenCalledTimes(1);
    expect(automaticSelectionRef.current).toBeNull();
    commit("c", [129, 35]);
    expect(easeTo).toHaveBeenLastCalledWith({ center: [129, 35], duration: 500 });
  });

  it("필터 결과를 불러오는 동안 보던 위치를 이전 목록에서 유지해 카메라가 되돌아가지 않는다", () => {
    const resolve = new Function(ts.transpileModule(
      declaration("resolveSelection") + "\nreturn resolveSelection;",
      { compilerOptions: { target: ts.ScriptTarget.ES2022 } },
    ).outputText)() as (
      visible: { location_id: string }[], selectedId: string, filterLoading: boolean, held: { location_id: string }[][],
    ) => { location_id: string } | undefined;
    const a = { location_id: "a" };
    const b = { location_id: "b" };
    // 로딩 중 빈 목록: 이전 source 목록(또는 기본 catalog)의 같은 위치를 유지한다.
    expect(resolve([], "a", true, [[b, a], []])).toBe(a);
    expect(resolve([], "a", true, [[], [a]])).toBe(a);
    // 로딩이 아니면 기존처럼 보이는 첫 위치로 대체하고, 빈 목록이면 선택이 없다.
    expect(resolve([b], "a", false, [[a]])).toBe(b);
    expect(resolve([], "a", false, [[a]])).toBeUndefined();
    expect(resolve([b, a], "a", true, [])).toBe(a);

    // 실제 effect를 id → (로딩) → 같은 id 순서로 replay해 easeTo가 다시 불리지 않는지 본다.
    const camera = effectWith("[mapReady, selectedLocationId]", [
      "automaticSelectionRef", "selectedLocationId", "selectedPositionRef", "map", "mapReady",
      "zoomOnFirstMoveRef",
    ], ["SELECTED_ZOOM"]);
    const easeTo = vi.fn();
    const refs = { automatic: { current: null }, position: { current: null as [number, number] | null }, zoom: { current: false } };
    let previous: string | undefined;
    const commit = (visible: { location_id: string }[], loading: boolean, held: { location_id: string }[][]) => {
      const selected = resolve(visible, "a", loading, held);
      refs.position.current = selected ? [127, 37] : null;
      if (selected?.location_id === previous) return;
      previous = selected?.location_id;
      camera(refs.automatic, previous, refs.position, { current: { easeTo } }, true, refs.zoom);
    };
    commit([a, b], false, []);
    expect(easeTo).toHaveBeenCalledTimes(1);
    commit([], true, [[a, b], []]);
    commit([{ location_id: "a" }], false, []);
    expect(easeTo).toHaveBeenCalledTimes(1);
  });

  it("기본 카메라로 시작했으면 첫 선택 이동에서만 확대한다", () => {
    const camera = effectWith("[mapReady, selectedLocationId]", [
      "automaticSelectionRef", "selectedLocationId", "selectedPositionRef", "map", "mapReady",
      "zoomOnFirstMoveRef",
    ], ["SELECTED_ZOOM"]);
    const easeTo = vi.fn();
    const zoomOnFirstMoveRef = { current: true };
    const move = (id: string, position: [number, number]) => camera(
      { current: null }, id, { current: position }, { current: { easeTo } }, true, zoomOnFirstMoveRef,
    );
    move("a", [127, 37]);
    expect(easeTo).toHaveBeenLastCalledWith({ center: [127, 37], duration: 500, zoom: 7.2 });
    move("b", [128, 36]);
    expect(easeTo).toHaveBeenLastCalledWith({ center: [128, 36], duration: 500 });
  });

  it("지도 center/zoom prop은 첫 카메라로 고정해 선택 effect만 카메라를 옮긴다", () => {
    const attributes = new Map<string, string>();
    function find(node: ts.Node) {
      if (ts.isJsxOpeningElement(node) && node.tagName.getText(tree) === "VWorldMapView") {
        for (const property of node.attributes.properties) {
          if (ts.isJsxAttribute(property) && property.initializer && ts.isJsxExpression(property.initializer)) {
            attributes.set(property.name.getText(tree), property.initializer.expression?.getText(tree) ?? "");
          }
        }
      }
      ts.forEachChild(node, find);
    }
    find(tree);
    expect(attributes.get("center")).toBe("initialCamera.center");
    expect(attributes.get("zoom")).toBe("initialCamera.zoom");
  });

  it("예보 종류만 선택하면 지난 예보를 현재 값처럼 조회하지 않고 안내한다", async () => {
    const detail = effectWith("[detailOpen, refreshToken, selectedLocationId, providerFilter, kindFilter]", [
      "setValues", "setForecast", "setMessage", "setForecastMessage", "setLastRefreshedAt",
      "setLoading", "setForecastLoading", "selectedLocationId", "getLatest", "getForecast",
      "forecastWindowStart", "providerFilter", "kindFilter", "detailOpen",
    ], ["CURRENT_KINDS", "includesCurrentKind"]);
    const mount = (kindFilter: string[] | undefined) => {
      const view = {
        setMessage: vi.fn(), setLoading: vi.fn(),
        getLatest: vi.fn(() => Promise.resolve({ data: [] })),
        getForecast: vi.fn(() => Promise.resolve({ data: [] })),
      };
      const cleanup = detail(vi.fn(), vi.fn(), view.setMessage, vi.fn(), vi.fn(), view.setLoading, vi.fn(),
        "a", view.getLatest, view.getForecast, () => "2026-10-27T00:00:00+09:00", undefined, kindFilter, true);
      return { ...view, cleanup };
    };
    const forecastOnly = mount(["short", "mid"]);
    expect(forecastOnly.getLatest).not.toHaveBeenCalled();
    expect(forecastOnly.getForecast).toHaveBeenCalledTimes(1);
    expect(forecastOnly.setMessage).toHaveBeenCalledWith("선택한 종류에는 현재 값이 없습니다.");
    expect(forecastOnly.setLoading).toHaveBeenLastCalledWith(false);
    forecastOnly.cleanup?.();
    for (const kinds of [undefined, ["short", "observed"], ["alert"]]) {
      const current = mount(kinds);
      expect(current.getLatest).toHaveBeenCalledTimes(1);
      current.cleanup?.();
    }
    // 남은 then/finally가 테스트 밖에서 돌지 않게 microtask를 비운다.
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  });
});
