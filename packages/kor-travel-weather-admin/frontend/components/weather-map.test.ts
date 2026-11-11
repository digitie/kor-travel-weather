import { readFileSync } from "node:fs";

import ts from "typescript";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";
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

describe("지역 대표 카드의 화면 충돌", () => {
  const node = tree.statements.find(statement => ts.isFunctionDeclaration(statement) && statement.name?.text === "layoutWeatherRegionCards");
  if (!node) throw new Error("대표 카드 배치 함수가 필요합니다.");
  type Rect = { left: number; top: number; width: number; height: number };
  const layout = new Function(`${ts.transpileModule(node.getText(tree).replace("export ", ""), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText}; return layoutWeatherRegionCards;`)() as
    (cards: Rect[], width: number, height: number, reserved: Rect[]) => { x: number; y: number; placed: boolean }[];
  it.each([[1120, 832], [351, 787]])("%sx%s에서도 상세/통계를 피하고 공간 부족을 목록에 알린다", (width, height) => {
    const cards = Array.from({ length: 29 }, () => ({ left: 200, top: 240, width: 132, height: 48 }));
    const obstacle = width > 400 ? { left: width - 360, top: 8, width: 350, height: height - 16 }
      : { left: 8, top: 8, width: width - 16, height: 100 };
    const offsets = layout(cards, width, height, [obstacle]);
    const placed = offsets.flatMap((offset, index) => offset.placed ? [{ ...cards[index], left: cards[index].left + offset.x, top: cards[index].top + offset.y }] : []);
    const overlap = (a: Rect, b: Rect) => a.left < b.left + b.width && a.left + a.width > b.left && a.top < b.top + b.height && a.top + a.height > b.top;
    expect(placed.length).toBeGreaterThan(1);
    for (const [index, rect] of placed.entries()) {
      expect(rect.left).toBeGreaterThanOrEqual(8); expect(rect.top).toBeGreaterThanOrEqual(8);
      expect(rect.left + rect.width).toBeLessThanOrEqual(width - 8);
      expect(rect.top + rect.height).toBeLessThanOrEqual(height - 8);
      expect(overlap(rect, obstacle)).toBe(false);
      for (const other of placed.slice(index + 1)) expect(overlap(rect, other)).toBe(false);
    }
    if (width < 400) expect(offsets.some(offset => !offset.placed)).toBe(true);
    else expect(placed).toHaveLength(cards.length);
    expect(cards.every(card => card.left === 200 && card.top === 240)).toBe(true);
  });
  it("같은 effect에서 목록을 접으면 mutation/resize가 다시 열지 않고 새 화면에서는 다시 자동 연다", () => {
    let effect: ts.Expression | undefined;
    function find(node: ts.Node) {
      if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect" && node.arguments[0]?.getText(tree).includes("const offsets = layoutWeatherRegionCards")) effect = node.arguments[0];
      ts.forEachChild(node, find);
    }
    find(tree);
    if (!effect) throw new Error("실제 지역 배치 effect가 필요합니다.");
    const frames: (() => void)[] = [];
    const mutations: (() => void)[] = [];
    class Observer { constructor(callback: () => void) { mutations.push(callback); } observe() {} disconnect() {} }
    const setOpen = vi.fn();
    const button = { offsetWidth: 130, offsetHeight: 130, tabIndex: 0 };
    const host = { getBoundingClientRect: () => ({ left: 0, top: 0 }), querySelector: (selector: string) => selector.includes("button") ? button : null,
      style: { setProperty() {} }, dataset: {}, setAttribute() {}, removeAttribute() {}, contains: () => false };
    const root = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }), querySelectorAll: () => [host], closest: () => null };
    const code = ts.transpileModule(`const effect = ${effect.getText(tree)}`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
    const run = () => new Function("map", "mapReady", "mode", "regionLevel", "markers", "setRegionListOpen", "layoutWeatherRegionCards", "requestAnimationFrame", "cancelAnimationFrame", "MutationObserver", "ResizeObserver", "document", `${code}; return effect();`)(
      { current: { getContainer: () => root, on() {}, off() {} } }, true, "map", "sido", [1], setOpen, layout,
      (callback: () => void) => { frames.push(callback); return frames.length; }, () => {}, Observer, Observer, { activeElement: null },
    ) as () => void;
    const cleanup = run(); frames.shift()!();
    expect(setOpen.mock.calls).toEqual([[true]]);
    setOpen(false);
    mutations[0](); frames.shift()!();
    mutations[1](); frames.shift()!();
    expect(setOpen.mock.calls).toEqual([[true], [false]]);
    cleanup(); const cleanupNext = run(); frames.shift()!();
    expect(setOpen.mock.calls.at(-1)).toEqual([true]); cleanupNext();
  });
  it("형제인 지역 목록 높이가 바뀌면 같은 effect에서 이동한 접기 버튼을 피해 다시 배치한다", () => {
    let effect: ts.Expression | undefined;
    function find(node: ts.Node) {
      if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect" && node.arguments[0]?.getText(tree).includes("const offsets = layoutWeatherRegionCards")) effect = node.arguments[0];
      ts.forEachChild(node, find);
    }
    find(tree);
    if (!effect) throw new Error("실제 지역 배치 effect가 필요합니다.");
    const frames: (() => void)[] = [];
    const observed = new Set<unknown>();
    let resized: () => void = () => {};
    class Mutation { observe() {} disconnect() {} }
    class Resize { constructor(callback: () => void) { resized = callback; } observe(target: unknown) { observed.add(target); } disconnect() {} }
    const offsets = new Map<string, string>();
    const access = {};
    let controlTop = 100;
    const control = { getBoundingClientRect: () => ({ left: 8, top: controlTop, width: 180, height: 44 }) };
    const stage = { querySelectorAll: (selector: string) => selector.includes(".weather-region-access > button") ? [control]
      : selector.includes(".weather-region-access") ? [access] : [] };
    const button = { offsetWidth: 120, offsetHeight: 48, tabIndex: 0 };
    const host = { getBoundingClientRect: () => ({ left: 10, top: 300 }), querySelector: (selector: string) => selector.includes("button") ? button : null,
      style: { setProperty: (key: string, value: string) => offsets.set(key, value) }, dataset: {}, setAttribute() {}, removeAttribute() {}, contains: () => false };
    const root = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 351, height: 400 }), querySelectorAll: () => [host], closest: () => stage };
    const setOpen = vi.fn();
    const code = ts.transpileModule(`const effect = ${effect.getText(tree)}`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
    const cleanup = new Function("map", "mapReady", "mode", "regionLevel", "markers", "setRegionListOpen", "layoutWeatherRegionCards", "requestAnimationFrame", "cancelAnimationFrame", "MutationObserver", "ResizeObserver", "document", `${code}; return effect();`)(
      { current: { getContainer: () => root, on() {}, off() {} } }, true, "map", "sido", [1], setOpen, layout,
      (callback: () => void) => { frames.push(callback); return frames.length; }, () => {}, Mutation, Resize, { activeElement: null },
    ) as () => void;
    try {
      frames.shift()!(); expect(offsets.get("--weather-region-y")).toBe("0px");
      // 목록은 map container의 형제이므로 높이 변경은 관찰 대상에 포함됐을 때만 콜백을 보낸다.
      controlTop = 300;
      if (observed.has(access)) resized();
      expect(frames).toHaveLength(1); frames.shift()!();
      expect(Number.parseFloat(offsets.get("--weather-region-y")!)).toBeLessThan(0);
      expect(setOpen).not.toHaveBeenCalled();
    } finally { cleanup(); }
  });
  it("portal 자식이 제거된 빈 marker host를 건너뛰고 남은 정상 카드의 측정과 배치를 유지한다", () => {
    let effect: ts.Expression | undefined;
    function find(node: ts.Node) {
      if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect" && node.arguments[0]?.getText(tree).includes("const offsets = layoutWeatherRegionCards")) effect = node.arguments[0];
      ts.forEachChild(node, find);
    }
    find(tree);
    if (!effect) throw new Error("실제 지역 배치 effect가 필요합니다.");
    const frames: (() => void)[] = [];
    class Observer { observe() {} disconnect() {} }
    const staleWrite = vi.fn(), validWrite = vi.fn();
    const button = { offsetWidth: 120, offsetHeight: 48, tabIndex: 0 };
    let portalRemoved = false;
    const stale = { getBoundingClientRect: () => ({ left: 0, top: 0 }), querySelector: () => portalRemoved ? null : button,
      style: { setProperty: staleWrite }, dataset: {}, setAttribute() {}, removeAttribute() {}, contains: () => false };
    const valid = { ...stale, getBoundingClientRect: () => ({ left: 80, top: 120 }), querySelector: (selector: string) => selector.includes("button") ? button : null,
      style: { setProperty: validWrite } };
    const root = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 351, height: 400 }), querySelectorAll: () => [stale, valid], closest: () => null };
    const code = ts.transpileModule(`const effect = ${effect.getText(tree)}`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
    const cleanup = new Function("map", "mapReady", "mode", "regionLevel", "markers", "setRegionListOpen", "layoutWeatherRegionCards", "requestAnimationFrame", "cancelAnimationFrame", "MutationObserver", "ResizeObserver", "document", `${code}; return effect();`)(
      { current: { getContainer: () => root, on() {}, off() {} } }, true, "map", "sido", [1, 2], vi.fn(), layout,
      (callback: () => void) => { frames.push(callback); return frames.length; }, () => {}, Observer, Observer, { activeElement: null },
    ) as () => void;
    try {
      // React가 portal 자식을 정리하고 MapLibre host는 아직 남아 있는 RAF 사이의 상태다.
      portalRemoved = true;
      expect(() => frames.shift()!()).not.toThrow();
      expect(staleWrite).not.toHaveBeenCalled();
      expect(validWrite).toHaveBeenCalledWith("--weather-region-x", expect.any(String));
      expect(validWrite).toHaveBeenCalledWith("--weather-region-y", expect.any(String));
    } finally { cleanup(); }
  });
});

describe("지도와 대체 목록의 같은 날씨 표시", () => {
  const source = readFileSync(new URL("./vworld-map-view.tsx", import.meta.url), "utf8");
  const ast = ts.createSourceFile("map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const node = ast.statements.find(statement => ts.isFunctionDeclaration(statement) && statement.name?.text === "WeatherMarkerLabel");
  if (!node) throw new Error("지도와 목록에서 공유할 표시 컴포넌트가 필요합니다.");
  const code = ts.transpileModule(node.getText(ast), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const label = new Function("require", "weatherConditionIcon", "weatherConditionLabel", `const exports = {}; ${code}; return exports.WeatherMarkerLabel;`)(
    () => jsxRuntime, () => "?", () => "정보 없음",
  ) as (props: { temperature: number | null; condition: string; alertCount: number; regionLabel: string }) => React.ReactElement;
  it.each([null, 21.7])("온도%s의 결측/반올림·대표 지역명·미확인 상태·특보 수를 보존한다", temperature => {
    const html = renderToStaticMarkup(label({ temperature, condition: "unknown", alertCount: 2, regionLabel: "서울특별시 중구" }));
    expect(html).toContain(temperature === null ? "—" : "22°");
    expect(html).toContain("서울특별시 중구"); expect(html).toContain("정보 없음"); expect(html).toContain("특보 2건");
  });
});
describe("마커 모델 참조 보존", () => {
  function renderer() {
    if (!markerMemo) throw new Error("marker 모델의 useMemo가 필요합니다.");
    const render = new Function(
      "regionLevel", "regional", "useMemo", "mapLocations", "selectedId", "summaries", "setSelectedId",
      "markerState", "markerTemperature", "markerCondition", "markerRefreshRef", "providerFilter", "refreshToken", "kindFilter", "setDetailOpen", "requestAnimationFrame", "detailRef",
      ts.transpileModule("return " + markerMemo.getText(tree), {
        compilerOptions: { target: ts.ScriptTarget.ES2022 },
      }).outputText,
    );
    let previous: unknown[] | undefined;
    let value: unknown;
    const memoize = (factory: () => unknown, deps: unknown[]) => {
      if (!previous || deps.some((dep, index) => !Object.is(dep, previous![index]))) {
        previous = deps;
        value = factory();
      }
      return value;
    };
    const emptyRegions: unknown[] = [];
    const select = vi.fn();
    const locations = [
      { location_id: "a", name: "가", longitude: 127, latitude: 37 },
      { location_id: "b", name: "나", longitude: 128, latitude: 36 },
    ];
    const call = (selectedId = "a", summaries = {}, points = locations) => render(
      null, emptyRegions, memoize, points, selectedId, summaries, select,
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
      && node.arguments[1]?.getText(tree).replace(/\s+/g, "").replace(/,]/g, "]") === deps.replace(/\s+/g, "")) found = node.arguments[0] as ts.ArrowFunction;
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
      "forecastWindowEnd", "FORECAST_DETAIL_LIMIT",
    ], ["CURRENT_KINDS", "includesCurrentKind"]);
    const mount = (kindFilter: string[] | undefined) => {
      const view = {
        setMessage: vi.fn(), setLoading: vi.fn(),
        getLatest: vi.fn(() => Promise.resolve({ data: [], meta: {} })),
        getForecast: vi.fn(() => Promise.resolve({ data: [] })),
      };
      const cleanup = detail(vi.fn(), vi.fn(), view.setMessage, vi.fn(), vi.fn(), view.setLoading, vi.fn(),
        "a", view.getLatest, view.getForecast, () => "2026-10-27T00:00:00+09:00", undefined, kindFilter, true,
        () => "2026-10-28T15:00:00.000Z", 5000);
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
