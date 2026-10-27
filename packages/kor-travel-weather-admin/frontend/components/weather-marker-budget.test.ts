import { readFileSync } from "node:fs";
import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getMarkerSummaries } from "../lib/api";

// 실제 두 effect를 실행한다. React의 범위 cleanup과 viewport 갱신은 분리한다.
const source = readFileSync(new URL("./weather-map.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("weather-map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const callbacks = new Map<string, ts.Expression>();
const constants: string[] = [];
function visit(node: ts.Node) {
  if (ts.isVariableStatement(node) && node.declarationList.declarations.some(
    (d) => ["MARKER_BATCH_SIZE", "MARKER_BATCH_CONCURRENCY"].includes(d.name.getText(tree)),
  )) constants.push(node.getText(tree));
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect") {
    callbacks.set(node.arguments[1]?.getText(tree) ?? "", node.arguments[0]);
  }
  ts.forEachChild(node, visit);
}
visit(tree);
function compileEffect(deps: string) {
  const callback = callbacks.get(deps);
  if (!callback || constants.length !== 2) throw new Error("마커 effect를 찾지 못했습니다.");
  return new Function("WEATHER_KINDS", "mapLocations", "mode", "refreshToken", "markerRefreshRef",
    "summariesRef", "setSummaries", "getMarkerSummaries", "providerFilter", "kindFilter",
    "setMarkerError", "markerQueueRef", "markerRetryToken",
    ts.transpileModule(constants.join("\n") + "\nreturn (" + callback.getText(tree) + ")();",
      { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText,
  ) as (...args: unknown[]) => (() => void) | undefined;
}
const scopeEffect = compileEffect("[mode, refreshToken, providerFilter, kindFilter]");
const viewportEffect = compileEffect("[mapLocations, mode, refreshToken, providerFilter, kindFilter, markerRetryToken]");
type Pending = { ids: string[]; signal?: AbortSignal; done: boolean; resolve: (response: Response) => void };
function setupFetch(ignoreAbort = false) {
  const pending: Pending[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, options: RequestInit) => new Promise<Response>((resolve, reject) => {
    const row: Pending = { ids: new URL(url, "http://localhost").searchParams.getAll("location_id"),
      signal: options.signal ?? undefined, done: false,
      resolve(response) { row.done = true; resolve(response); } };
    pending.push(row);
    if (!ignoreAbort) row.signal?.addEventListener("abort", () => {
      if (!row.done) { row.done = true; reject(new DOMException("취소", "AbortError")); }
    }, { once: true });
  })));
  return pending;
}
function ids(count: number, start = 0) { return Array.from({ length: count }, (_, i) => "a" + (i + start)); }
function mount(initial = ids(240)) {
  const setSummaries = vi.fn(); const setMarkerError = vi.fn();
  const summariesRef = { current: {} as Record<string, unknown> };
  const markerRefreshRef = { current: "" }; const markerQueueRef = { current: null };
  let cleanup: (() => void) | undefined; let lastScope = "";
  let points = initial; let retry = 0;
  let options: { mode: string; token: number; providers?: string[]; kinds?: string[] } = { mode: "map", token: 0 };
  function render(next = points, change: Partial<typeof options> = {}) {
    points = next; options = { ...options, ...change };
    const args = [["alert", "nowcast", "ultra_short", "short", "mid", "observed"].map((value) => ({ value })),
      points.map((location_id) => ({ location_id })), options.mode, options.token, markerRefreshRef,
      summariesRef, setSummaries, getMarkerSummaries, options.providers, options.kinds, setMarkerError,
      markerQueueRef, retry];
    const scope = JSON.stringify(options);
    if (scope !== lastScope) { cleanup?.(); lastScope = scope; cleanup = scopeEffect(...args); }
    viewportEffect(...args);
  }
  render();
  return { render, retry() { retry += 1; render(); }, cleanup() { cleanup?.(); },
    setSummaries, summariesRef, setMarkerError };
}
function respond(row: Pending, status = 200) {
  row.resolve(new Response(JSON.stringify({ data: row.ids.map((location_id) => ({ location_id, latest: [],
    forecast: [], alerts: [], measurement_point: null })), meta: {} }),
    { status, headers: { "content-type": "application/json" } }));
}
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
afterEach(() => vi.unstubAllGlobals());

describe("필터 범위별 동시2 marker queue", () => {
  it("실제 재조회 버튼은 상세·전체 refresh 대신 marker retry만 증가시킨다", () => {
    let callback: ts.Expression | undefined;
    function find(node: ts.Node) {
      if (ts.isJsxElement(node) && node.children.some((child) => ts.isJsxText(child)
        && child.text.trim() === "날씨 다시 조회")) {
        const attribute = node.openingElement.attributes.properties.find((item) =>
          ts.isJsxAttribute(item) && item.name.getText(tree) === "onClick");
        if (attribute && ts.isJsxAttribute(attribute) && attribute.initializer
          && ts.isJsxExpression(attribute.initializer)) callback = attribute.initializer.expression;
      }
      ts.forEachChild(node, find);
    }
    find(tree);
    if (!callback) throw new Error("실패 재조회 버튼이 필요합니다.");
    const retry = vi.fn(); const refresh = vi.fn();
    new Function("setMarkerRetryToken", "setRefreshToken", ts.transpileModule(
      "(" + callback.getText(tree) + ")();", { compilerOptions: { target: ts.ScriptTarget.ES2022 } },
    ).outputText)(retry, refresh);
    expect(retry).toHaveBeenCalledTimes(1); expect(retry.mock.calls[0][0](7)).toBe(8);
    expect(refresh).not.toHaveBeenCalled();
  });
  it("692지점을60이하로한번씩보내고빈worker를즉시채운다", async () => {
    const pending = setupFetch(); const view = mount(ids(692));
    expect(pending).toHaveLength(2); let maximum = 0;
    while (pending.some((row) => !row.done)) {
      maximum = Math.max(maximum, pending.filter((row) => !row.done).length);
      respond([...pending].reverse().find((row) => !row.done)!); await flush();
      expect(pending.filter((row) => !row.done).length).toBeLessThanOrEqual(2);
    }
    expect(maximum).toBe(2); expect(pending).toHaveLength(12);
    expect(pending.every((row) => row.ids.length <= 60)).toBe(true);
    expect(new Set(pending.flatMap((row) => row.ids)).size).toBe(692);
    expect(pending.flatMap((row) => row.ids)).toHaveLength(692);
    expect(Object.keys(view.summariesRef.current)).toHaveLength(692); view.cleanup();
  });
  it("viewport중첩은진행중120지점을취소하거나중복배정하지않는다", async () => {
    const pending = setupFetch(); const view = mount(ids(180));
    view.render(ids(180, 1)); view.render(ids(180, 2));
    expect(pending).toHaveLength(2); expect(pending.some((row) => row.signal?.aborted)).toBe(false);
    respond(pending[0]); await flush(); expect(pending).toHaveLength(3);
    expect(pending[2].ids).toEqual(ids(60, 120));
    respond(pending[1]); await flush(); expect(pending[3].ids).toEqual(["a180", "a181"]);
    expect(new Set(pending.flatMap((row) => row.ids)).size).toBe(pending.flatMap((row) => row.ids).length);
    view.cleanup();
  });
  it("벗어난viewport는진행중요청만완주하고옛대기목록은시작하지않는다", async () => {
    const pending = setupFetch(); const view = mount(ids(360));
    view.render(["new"]); respond(pending[0]); await flush();
    expect(pending).toHaveLength(3); expect(pending[2].ids).toEqual(["new"]);
    view.cleanup(); await flush(); expect(pending).toHaveLength(3);
  });
  it.each(["providers", "kinds"])("%s변경은취소하고늦은응답도새cache에쓰지않는다", async (filter) => {
    const pending = setupFetch(true); const view = mount(["same"]);
    view.render(["same"], { [filter]: ["changed"] });
    expect(pending[0].signal?.aborted).toBe(true); expect(pending).toHaveLength(2);
    respond(pending[1]); await flush(); const snapshot = view.summariesRef.current;
    const count = view.setSummaries.mock.calls.length; respond(pending[0]); await flush();
    expect(view.summariesRef.current).toBe(snapshot); expect(view.setSummaries).toHaveBeenCalledTimes(count);
    view.cleanup();
  });
  it("unmount는fetch2개를취소하고후속wave및상태쓰기를막는다", async () => {
    const pending = setupFetch(); const view = mount(); const count = view.setSummaries.mock.calls.length;
    view.cleanup(); await flush(); expect(pending).toHaveLength(2);
    expect(pending.every((row) => row.signal?.aborted && row.done)).toBe(true);
    expect(view.setSummaries).toHaveBeenCalledTimes(count);
  });
  it.each(["mode", "refresh"])("%s변경은진행중요청을취소한다", async (change) => {
    const pending = setupFetch(); const view = mount();
    view.render(undefined, change === "mode" ? { mode: "list" } : { token: 1 });
    expect(pending.slice(0, 2).every((row) => row.signal?.aborted)).toBe(true);
    expect(pending).toHaveLength(change === "mode" ? 2 : 4); view.cleanup(); await flush();
  });
  it("실패chunk만retry하고성공cache나성공ID를다시조회하지않는다", async () => {
    const pending = setupFetch(); const view = mount(ids(120));
    respond(pending[0]); respond(pending[1], 504); await flush();
    expect(pending).toHaveLength(2); expect(Object.keys(view.summariesRef.current)).toHaveLength(60);
    const good = view.summariesRef.current.a0; view.retry();
    expect(pending).toHaveLength(3); expect(pending[2].ids).toEqual(ids(60, 60));
    expect(view.summariesRef.current.a0).toBe(good); respond(pending[2]); await flush();
    expect(Object.keys(view.summariesRef.current)).toHaveLength(120);
    expect(view.setMarkerError).toHaveBeenLastCalledWith(""); view.cleanup();
  });
  it("부분응답은무한반복하지않고누락만수동retry한다", async () => {
    const pending = setupFetch(); const view = mount(["a", "b"]);
    pending[0].resolve(new Response(JSON.stringify({ data: [{ location_id: "a", latest: [], alerts: [] }] }),
      { headers: { "content-type": "application/json" } })); await flush();
    expect(pending).toHaveLength(1); view.retry(); expect(pending[1].ids).toEqual(["b"]); view.cleanup();
  });
  it("viewport밖실패는오류를남기지않고다시보이면표시한다", async () => {
    const pending = setupFetch(); const view = mount(["a"]); view.render(["b"]);
    respond(pending[0], 504); await flush(); expect(view.setMarkerError).toHaveBeenLastCalledWith("");
    respond(pending[1]); await flush(); view.render(["a"]);
    expect(view.setMarkerError.mock.lastCall?.[0]).toBeTruthy(); expect(pending).toHaveLength(2); view.cleanup();
  });
});
