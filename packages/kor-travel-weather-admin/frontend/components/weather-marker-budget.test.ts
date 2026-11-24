import { readFileSync } from "node:fs";
import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getCompactMarkers } from "../lib/api";
import {
  createMarkerQueue,
  MARKER_BATCH_SIZE,
  MARKER_ERROR_RETRY_MS,
  MARKER_MISSING_RETRY_MS,
  MARKER_PARTIAL_ERROR,
  MARKER_RETRY_MAX_MS,
  markerFetchOrder,
  markerRetryDelay,
} from "../lib/weather-marker-queue";
import { viewportCenter } from "../lib/weather-viewport";

// 실제 두 effect를 실행한다. React의 범위 cleanup과 viewport 갱신은 분리한다.
const source = readFileSync(new URL("./weather-map.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("weather-map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const callbacks = new Map<string, ts.Expression>();
function visit(node: ts.Node) {
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect") {
    callbacks.set(node.arguments[1]?.getText(tree).replace(/\s+/g, "").replace(/,]/g, "]") ?? "", node.arguments[0]);
  }
  ts.forEachChild(node, visit);
}
visit(tree);
const NAMES = ["WEATHER_KINDS", "mapLocations", "mode", "refreshToken", "markerScopeRef", "markerFreshRef",
  "summariesRef", "setSummaries", "getCompactMarkers", "createMarkerQueue", "markerFetchOrder", "viewportCenter",
  "providerFilter", "kindFilter", "setMarkerError", "markerQueueRef", "markerRetryToken", "effectiveViewport"];
function compileEffect(deps: string) {
  const callback = callbacks.get(deps.replace(/\s+/g, ""));
  if (!callback) throw new Error("마커 effect를 찾지 못했습니다.");
  return new Function(...NAMES,
    ts.transpileModule("return (" + callback.getText(tree) + ")();",
      { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText,
  ) as (...args: unknown[]) => (() => void) | undefined;
}
const scopeEffect = compileEffect("[mode, refreshToken, providerFilter, kindFilter]");
const viewportEffect = compileEffect(
  "[mapLocations, effectiveViewport, mode, refreshToken, providerFilter, kindFilter, markerRetryToken]",
);
type Pending = { ids: string[]; url: URL; signal?: AbortSignal; done: boolean; resolve: (response: Response) => void };
function setupFetch(ignoreAbort = false) {
  const pending: Pending[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, options: RequestInit) => new Promise<Response>((resolve, reject) => {
    const parsed = new URL(url, "http://localhost");
    const row: Pending = { ids: parsed.searchParams.getAll("location_id"), url: parsed,
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
type Viewport = { west: number; east: number; south: number; north: number } | null;
function mount(initial = ids(240)) {
  const setSummaries = vi.fn(); const setMarkerError = vi.fn();
  const summariesRef = { current: {} as Record<string, unknown> };
  const markerScopeRef = { current: "" };
  const markerFreshRef = { current: { generation: "", ids: new Set<string>() } };
  const markerQueueRef = { current: null };
  let cleanup: (() => void) | undefined; let lastScope = "";
  let points = initial; let retry = 0;
  let positions: Record<string, [number, number]> = {};
  let viewport: Viewport = null;
  let options: { mode: string; token: number; providers?: string[]; kinds?: string[] } = { mode: "map", token: 0 };
  function render(next = points, change: Partial<typeof options> = {}) {
    points = next; options = { ...options, ...change };
    const args = [["alert", "nowcast", "ultra_short", "short", "mid", "observed"].map((value) => ({ value })),
      points.map((location_id) => ({ location_id, longitude: positions[location_id]?.[0] ?? 127,
        latitude: positions[location_id]?.[1] ?? 37 })),
      options.mode, options.token, markerScopeRef, markerFreshRef,
      summariesRef, setSummaries, getCompactMarkers, createMarkerQueue, markerFetchOrder, viewportCenter,
      options.providers, options.kinds, setMarkerError, markerQueueRef, retry, viewport];
    const scope = JSON.stringify(options);
    if (scope !== lastScope) { cleanup?.(); lastScope = scope; cleanup = scopeEffect(...args); }
    viewportEffect(...args);
  }
  return { render, retry() { retry += 1; render(); }, cleanup() { cleanup?.(); },
    place(next: Record<string, [number, number]>, view: Viewport) { positions = next; viewport = view; },
    start() { render(); return this; },
    setSummaries, summariesRef, setMarkerError };
}
function respond(row: Pending, status = 200, only?: string[]) {
  row.resolve(new Response(JSON.stringify({ data: (only ?? row.ids).map((location_id) => ({ location_id, latest: [],
    forecast: [], alerts: [], measurement_point: null })), meta: {} }),
    { status, headers: { "content-type": "application/json" } }));
}
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

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
  it("관리 지도는 compact 마커 응답을 요청한다", () => {
    const pending = setupFetch(); const view = mount(["a"]).start();
    expect(pending[0].url.pathname).toBe("/api/weather/v1/weather/markers");
    expect(pending[0].url.searchParams.get("view")).toBe("compact");
    expect(pending[0].url.searchParams.getAll("kind")).toHaveLength(6);
    view.cleanup();
  });
  it("692지점을60이하로한번씩보내고빈worker를즉시채운다", async () => {
    const pending = setupFetch(); const view = mount(ids(692)).start();
    expect(pending).toHaveLength(2); let maximum = 0;
    while (pending.some((row) => !row.done)) {
      maximum = Math.max(maximum, pending.filter((row) => !row.done).length);
      respond([...pending].reverse().find((row) => !row.done)!); await flush();
      expect(pending.filter((row) => !row.done).length).toBeLessThanOrEqual(2);
    }
    expect(maximum).toBe(2); expect(pending).toHaveLength(12);
    expect(pending.every((row) => row.ids.length <= MARKER_BATCH_SIZE)).toBe(true);
    expect(new Set(pending.flatMap((row) => row.ids)).size).toBe(692);
    expect(pending.flatMap((row) => row.ids)).toHaveLength(692);
    expect(Object.keys(view.summariesRef.current)).toHaveLength(692); view.cleanup();
  });
  it("viewport중첩은진행중120지점을취소하거나중복배정하지않는다", async () => {
    const pending = setupFetch(); const view = mount(ids(180)).start();
    view.render(ids(180, 1)); view.render(ids(180, 2));
    expect(pending).toHaveLength(2); expect(pending.some((row) => row.signal?.aborted)).toBe(false);
    respond(pending[0]); await flush(); expect(pending).toHaveLength(3);
    expect(pending[2].ids).toEqual(ids(60, 120));
    respond(pending[1]); await flush(); expect(pending[3].ids).toEqual(["a180", "a181"]);
    expect(new Set(pending.flatMap((row) => row.ids)).size).toBe(pending.flatMap((row) => row.ids).length);
    view.cleanup();
  });
  it("벗어난viewport는진행중요청만완주하고옛대기목록은시작하지않는다", async () => {
    const pending = setupFetch(); const view = mount(ids(360)).start();
    view.render(["new"]); respond(pending[0]); await flush();
    expect(pending).toHaveLength(3); expect(pending[2].ids).toEqual(["new"]);
    view.cleanup(); await flush(); expect(pending).toHaveLength(3);
  });
  it.each(["providers", "kinds"])("%s변경은취소하고늦은응답도새cache에쓰지않는다", async (filter) => {
    const pending = setupFetch(true); const view = mount(["same"]).start();
    view.render(["same"], { [filter]: ["changed"] });
    expect(pending[0].signal?.aborted).toBe(true); expect(pending).toHaveLength(2);
    respond(pending[1]); await flush(); const snapshot = view.summariesRef.current;
    const count = view.setSummaries.mock.calls.length; respond(pending[0]); await flush();
    expect(view.summariesRef.current).toBe(snapshot); expect(view.setSummaries).toHaveBeenCalledTimes(count);
    view.cleanup();
  });
  it("unmount는fetch2개를취소하고후속wave및상태쓰기를막는다", async () => {
    const pending = setupFetch(); const view = mount().start(); const count = view.setSummaries.mock.calls.length;
    view.cleanup(); await flush(); expect(pending).toHaveLength(2);
    expect(pending.every((row) => row.signal?.aborted && row.done)).toBe(true);
    expect(view.setSummaries).toHaveBeenCalledTimes(count);
  });
  it.each(["mode", "refresh"])("%s변경은진행중요청을취소한다", async (change) => {
    const pending = setupFetch(); const view = mount().start();
    view.render(undefined, change === "mode" ? { mode: "list" } : { token: 1 });
    expect(pending.slice(0, 2).every((row) => row.signal?.aborted)).toBe(true);
    expect(pending).toHaveLength(change === "mode" ? 2 : 4); view.cleanup(); await flush();
  });
  // 502/503/504는 request()가 먼저 다시 보낸다(lib/api-retry.test.ts). 아래 실패 시험은 최종 실패(500)를 쓴다.
  it("실패chunk만retry하고성공cache나성공ID를다시조회하지않는다", async () => {
    const pending = setupFetch(); const view = mount(ids(120)).start();
    respond(pending[0]); respond(pending[1], 500); await flush();
    expect(pending).toHaveLength(2); expect(Object.keys(view.summariesRef.current)).toHaveLength(60);
    expect(view.setMarkerError).toHaveBeenLastCalledWith(MARKER_PARTIAL_ERROR);
    const good = view.summariesRef.current.a0; view.retry();
    expect(pending).toHaveLength(3); expect(pending[2].ids).toEqual(ids(60, 60));
    expect(view.summariesRef.current.a0).toBe(good); respond(pending[2]); await flush();
    expect(Object.keys(view.summariesRef.current)).toHaveLength(120);
    expect(view.setMarkerError).toHaveBeenLastCalledWith(""); view.cleanup();
  });
  it("부분응답은즉시반복하지않고누락만수동retry한다", async () => {
    const pending = setupFetch(); const view = mount(["a", "b"]).start();
    respond(pending[0], 200, ["a"]); await flush();
    expect(pending).toHaveLength(1); view.retry(); expect(pending[1].ids).toEqual(["b"]); view.cleanup();
  });
  it("viewport밖실패는오류를남기지않고다시보이면표시한다", async () => {
    const pending = setupFetch(); const view = mount(["a"]).start(); view.render(["b"]);
    respond(pending[0], 500); await flush(); expect(view.setMarkerError).toHaveBeenLastCalledWith("");
    respond(pending[1]); await flush(); view.render(["a"]);
    expect(view.setMarkerError.mock.lastCall?.[0]).toBeTruthy(); expect(pending).toHaveLength(2); view.cleanup();
  });
  it("일시504chunk는같은worker가backoff뒤다시보내고성공하면오류없이채운다", async () => {
    vi.useFakeTimers(); vi.spyOn(Math, "random").mockReturnValue(0);
    const pending = setupFetch(); const view = mount(ids(120)).start();
    respond(pending[0]); respond(pending[1], 504); await vi.advanceTimersByTimeAsync(499);
    expect(pending).toHaveLength(2); expect(Object.keys(view.summariesRef.current)).toHaveLength(60);
    await vi.advanceTimersByTimeAsync(1);
    expect(pending).toHaveLength(3); expect(pending[2].ids).toEqual(ids(60, 60));
    respond(pending[2]); await vi.advanceTimersByTimeAsync(0);
    expect(Object.keys(view.summariesRef.current)).toHaveLength(120);
    expect(view.setMarkerError).toHaveBeenLastCalledWith(""); view.cleanup();
  });
  it("재시도대기중범위가바뀌면옛chunk를다시보내지않는다", async () => {
    vi.useFakeTimers(); vi.spyOn(Math, "random").mockReturnValue(0);
    const pending = setupFetch(); const view = mount(["a"]).start();
    respond(pending[0], 503); await vi.advanceTimersByTimeAsync(100);
    view.render(["a"], { kinds: ["changed"] }); await vi.runAllTimersAsync();
    // 옛 범위 signal은 대기 중 취소되어 3번째 요청(옛 chunk 재전송)이 생기지 않는다.
    expect(pending[0].signal?.aborted).toBe(true);
    expect(pending).toHaveLength(2); expect(pending[1].signal?.aborted).toBe(false);
    view.cleanup();
  });
});

describe("마커 실패의 시간 기반 재시도(B6)", () => {
  it("최종 실패한 위치는 영구 공백이 아니라 대기 뒤 자동으로 다시 보낸다", async () => {
    vi.useFakeTimers();
    const pending = setupFetch(); const view = mount(["a", "b"]).start();
    respond(pending[0], 500); await vi.advanceTimersByTimeAsync(0);
    expect(view.setMarkerError).toHaveBeenLastCalledWith(MARKER_PARTIAL_ERROR);
    await vi.advanceTimersByTimeAsync(MARKER_ERROR_RETRY_MS - 1);
    expect(pending).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(pending).toHaveLength(2); expect(pending[1].ids).toEqual(["a", "b"]);
    respond(pending[1]); await vi.advanceTimersByTimeAsync(0);
    expect(Object.keys(view.summariesRef.current)).toEqual(["a", "b"]);
    expect(view.setMarkerError).toHaveBeenLastCalledWith("");
    // 성공한 뒤에는 더 보내지 않는다.
    await vi.advanceTimersByTimeAsync(MARKER_RETRY_MAX_MS * 2);
    expect(pending).toHaveLength(2); view.cleanup();
  });
  it("연속 실패는 대기를 두 배로 늘리고 상한을 넘지 않는다", async () => {
    vi.useFakeTimers();
    const pending = setupFetch(); const view = mount(["a"]).start();
    respond(pending[0], 500); await vi.advanceTimersByTimeAsync(MARKER_ERROR_RETRY_MS);
    expect(pending).toHaveLength(2);
    respond(pending[1], 500); await vi.advanceTimersByTimeAsync(MARKER_ERROR_RETRY_MS * 2 - 1);
    expect(pending).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1); expect(pending).toHaveLength(3);
    expect(markerRetryDelay(1, false)).toBe(MARKER_ERROR_RETRY_MS);
    expect(markerRetryDelay(3, false)).toBe(MARKER_ERROR_RETRY_MS * 4);
    expect(markerRetryDelay(30, false)).toBe(MARKER_RETRY_MAX_MS);
    expect(markerRetryDelay(30, true)).toBe(MARKER_RETRY_MAX_MS);
    view.cleanup();
  });
  it("응답에서 빠진 위치는 긴 대기 뒤에만 다시 묻는다", async () => {
    vi.useFakeTimers();
    const pending = setupFetch(); const view = mount(["a", "b"]).start();
    respond(pending[0], 200, ["a"]); await vi.advanceTimersByTimeAsync(MARKER_MISSING_RETRY_MS - 1);
    expect(pending).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(pending).toHaveLength(2); expect(pending[1].ids).toEqual(["b"]);
    view.cleanup();
  });
  it("화면 밖으로 나간 실패 위치는 대기가 끝나도 보내지 않고 다시 보이면 보낸다", async () => {
    vi.useFakeTimers();
    const pending = setupFetch(); const view = mount(["a"]).start();
    respond(pending[0], 500); await vi.advanceTimersByTimeAsync(0);
    view.render(["b"]); respond(pending[1]); await vi.advanceTimersByTimeAsync(MARKER_ERROR_RETRY_MS * 2);
    expect(pending).toHaveLength(2);
    view.render(["a", "b"]); expect(pending).toHaveLength(3); expect(pending[2].ids).toEqual(["a"]);
    view.cleanup();
  });
  it("범위가 바뀌면 옛 큐의 재시도 timer는 요청을 만들지 않는다", async () => {
    vi.useFakeTimers();
    const pending = setupFetch(); const view = mount(["a"]).start();
    respond(pending[0], 500); await vi.advanceTimersByTimeAsync(0);
    view.render(["a"], { providers: ["p"] }); respond(pending[1]); await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(MARKER_RETRY_MAX_MS);
    expect(pending).toHaveLength(2); view.cleanup();
  });
});

describe("새로고침과 요청 순서(B12)", () => {
  it("새로고침은 그린 마커를 지우지 않고 다시 받아 도착한 위치만 바꾼다", async () => {
    const pending = setupFetch(); const view = mount(["a", "b"]).start();
    respond(pending[0]); await flush();
    const before = view.summariesRef.current; const oldA = before.a;
    view.setSummaries.mockClear();
    view.render(undefined, { token: 1 });
    // 캐시를 비우지 않는다(빈 객체로 다시 그리지 않는다).
    expect(view.summariesRef.current).toBe(before); expect(view.setSummaries).not.toHaveBeenCalled();
    expect(pending).toHaveLength(2); expect(pending[1].ids).toEqual(["a", "b"]);
    respond(pending[1], 200, ["a"]); await flush();
    expect(view.summariesRef.current.a).not.toBe(oldA); expect(view.summariesRef.current.b).toBeTruthy();
    view.cleanup();
  });
  it("필터 범위 변경은 이전 범위의 마커를 지운다", async () => {
    const pending = setupFetch(); const view = mount(["a"]).start();
    respond(pending[0]); await flush();
    view.render(undefined, { kinds: ["alert"] });
    expect(view.summariesRef.current).toEqual({}); expect(view.setSummaries).toHaveBeenLastCalledWith({});
    view.cleanup();
  });
  it("같은 세대에서 지도↔목록을 오가면 받은 위치를 다시 요청하지 않는다", async () => {
    const pending = setupFetch(); const view = mount(["a", "b"]).start();
    respond(pending[0]); await flush();
    view.render(undefined, { mode: "list" }); view.render(undefined, { mode: "map" });
    expect(pending).toHaveLength(1); view.cleanup();
  });
  it("화면 중심에서 가까운 위치부터 첫 묶음으로 보낸다", () => {
    const pending = setupFetch();
    const view = mount(ids(130));
    const positions: Record<string, [number, number]> = {};
    // a0이 가장 멀고 a129가 중심에 가장 가깝다.
    ids(130).forEach((id, index) => { positions[id] = [127 + (129 - index) * 0.01, 37]; });
    view.place(positions, { west: 126.9, east: 127.1, south: 36.9, north: 37.1 });
    view.start();
    expect(pending[0].ids[0]).toBe("a129");
    expect(pending[0].ids).toEqual(ids(60, 70).reverse());
    expect(pending[1].ids).toEqual(ids(60, 10).reverse());
    view.cleanup();
  });
  it("중심을 모르면 받은 순서를 유지하고 같은 거리는 원래 순서를 지킨다", () => {
    const rows = [{ location_id: "x", longitude: 128, latitude: 37 }, { location_id: "y", longitude: 126, latitude: 37 },
      { location_id: "z", longitude: 127, latitude: 37 }];
    expect(markerFetchOrder(rows, null)).toEqual(["x", "y", "z"]);
    expect(markerFetchOrder(rows, [127, 37])).toEqual(["z", "x", "y"]);
  });
});
