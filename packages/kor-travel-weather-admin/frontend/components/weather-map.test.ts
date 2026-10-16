import { readFileSync } from "node:fs";

import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getMarkerSummaries } from "../lib/api";

// 실제 effect callback을 실행한다. DOM/지도 대신 React의 cleanup→새 effect 순서만 명시한다.
const source = readFileSync(new URL("./weather-map.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("weather-map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback: ts.ArrowFunction | undefined;
let markerMemo: ts.Expression | undefined;
const constants: string[] = [];
function visit(node: ts.Node) {
  if (ts.isVariableStatement(node) && node.declarationList.declarations.some(
    (declaration) => ["MARKER_BATCH_SIZE", "MARKER_BATCH_CONCURRENCY"].includes(declaration.name.getText(tree)),
  )) constants.push(node.getText(tree));
  if (ts.isVariableDeclaration(node) && node.name.getText(tree) === "markers") {
    markerMemo = node.initializer;
  }
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect"
    && node.arguments[1]?.getText(tree) === "[mapLocations, mode, refreshToken]") {
    callback = node.arguments[0] as ts.ArrowFunction;
  }
  ts.forEachChild(node, visit);
}
visit(tree);
if (!callback || constants.length !== 2) throw new Error("마커 effect를 찾지 못했습니다.");
const javascript = ts.transpileModule(
  constants.join("\n") + "\nreturn (" + callback.getText(tree) + ")();",
  { compilerOptions: { target: ts.ScriptTarget.ES2022 } },
).outputText;
const effect = new Function(
  "mapLocations", "mode", "refreshToken", "markerRefreshRef", "summariesRef",
  "setSummaries", "getMarkerSummaries", javascript,
) as (...args: unknown[]) => (() => void) | undefined;

type Pending = {
  ids: string[];
  signal: AbortSignal | undefined;
  resolve: (response: Response) => void;
  done: boolean;
};
function setupFetch() {
  const pending: Pending[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, options: RequestInit) => new Promise<Response>(
    (resolve, reject) => {
      const row: Pending = {
        ids: new URL(url, "http://localhost").searchParams.getAll("location_id"),
        signal: options.signal ?? undefined,
        done: false,
        resolve(response) { row.done = true; resolve(response); },
      };
      pending.push(row);
      const cancel = () => {
        if (row.done) return;
        row.done = true;
        reject(new DOMException("취소", "AbortError"));
      };
      if (row.signal?.aborted) cancel();
      else row.signal?.addEventListener("abort", cancel, { once: true });
    },
  )));
  return pending;
}
function mount(count = 240, options: { mode?: string; token?: number; prefix?: string } = {}) {
  const setSummaries = vi.fn();
  const summariesRef = { current: {} };
  const token = options.token ?? 0;
  const cleanup = effect(
    Array.from({ length: count }, (_, index) => ({ location_id: (options.prefix ?? "a") + index })),
    options.mode ?? "map", token, { current: token }, summariesRef, setSummaries, getMarkerSummaries,
  );
  return { cleanup, setSummaries, summariesRef };
}
function envelope(id = "a0") {
  return new Response(JSON.stringify({
    data: [{ location_id: id, latest: [], alerts: [], measurement_point: null }],
    meta: { request_id: "test", generated_at: "test", duration_ms: 0 },
  }), { headers: { "content-type": "application/json" } });
}
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
afterEach(() => vi.unstubAllGlobals());

describe("마커 effect 요청 취소", () => {
  it("unmount는 진행 중 3개 실제 fetch signal을 모두 취소하고 후속 wave/상태 쓰기를 막는다", async () => {
    const pending = setupFetch();
    const view = mount();
    expect(pending).toHaveLength(3);
    view.cleanup?.();
    expect(pending.every((row) => row.signal?.aborted && row.done)).toBe(true);
    await flush();
    expect(pending).toHaveLength(3);
    expect(view.setSummaries).not.toHaveBeenCalled();
    expect(view.summariesRef.current).toEqual({});
  });

  it("파라미터가 바뀌면 이전 fetch는 남지 않고 새 effect만 3개 실행한다", async () => {
    const pending = setupFetch();
    const old = mount();
    old.cleanup?.();
    const current = mount(240, { prefix: "b" });
    expect(pending).toHaveLength(6);
    expect(pending.slice(0, 3).every((row) => row.signal?.aborted && row.done)).toBe(true);
    expect(pending.filter((row) => !row.done)).toHaveLength(3);
    pending.slice(3).forEach((row) => row.resolve(envelope("b0")));
    await flush();
    expect(old.setSummaries).not.toHaveBeenCalled();
    expect(current.setSummaries).toHaveBeenCalledTimes(3);
    current.cleanup?.();
    await flush();
    expect(pending.filter((row) => !row.done)).toHaveLength(0);
  });

  it.each(["mode", "refresh"])("%s 변경 cleanup도 기존 요청을 취소한다", async (change) => {
    const pending = setupFetch();
    const old = mount();
    old.cleanup?.();
    const current = mount(240, { mode: change === "mode" ? "list" : "map", token: 1 });
    await flush();
    expect(pending).toHaveLength(change === "mode" ? 3 : 6);
    expect(pending.slice(0, 3).every((row) => row.signal?.aborted && row.done)).toBe(true);
    current.cleanup?.();
    await flush();
    expect(pending.filter((row) => !row.done)).toHaveLength(0);
  });

  it("완료한 chunk는 즉시 보존하고 cleanup 뒤 늦은 결과만 차단한다", async () => {
    const pending = setupFetch();
    const view = mount();
    pending[0].resolve(envelope());
    await flush();
    view.cleanup?.();
    await flush();
    expect(pending.filter((row) => !row.done)).toHaveLength(0);
    expect(view.setSummaries).toHaveBeenCalledTimes(1);
    expect(view.summariesRef.current).toHaveProperty("a0");
    expect(pending).toHaveLength(4);
  });

  it("취소를 무시한 fetch의 늦은 성공도 기존 publication guard가 차단한다", async () => {
    const pending: Array<(response: Response) => void> = [];
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => pending.push(resolve))));
    const view = mount();
    view.cleanup?.();
    pending.forEach((resolve) => resolve(envelope()));
    await flush();
    expect(view.setSummaries).not.toHaveBeenCalled();
    expect(view.summariesRef.current).toEqual({});
    expect(pending).toHaveLength(3);
  });

  it("정상 결과와 실패 격리 및 기존 batch 상한을 유지한다", async () => {
    const pending = setupFetch();
    const view = mount(180);
    expect(pending).toHaveLength(3);
    expect(vi.mocked(fetch).mock.calls.every(([url]) =>
      new URL(String(url), "http://localhost").searchParams.getAll("location_id").length === 60,
    )).toBe(true);
    pending[0].resolve(envelope());
    pending[1].resolve(new Response("Gateway", { status: 504 }));
    pending[2].resolve(envelope("a120"));
    await flush();
    expect(view.summariesRef.current).toHaveProperty("a0");
    expect(view.summariesRef.current).toHaveProperty("a120");
    expect(view.setSummaries).toHaveBeenCalledTimes(2);
    view.cleanup?.();
  });
});


describe("상한이 있는 마커 worker queue", () => {
  it("느린 두 요청이 남아도 끝난 worker가 다음 chunk를 바로 시작한다", async () => {
    const pending = setupFetch();
    const view = mount(360);
    expect(pending).toHaveLength(3);
    pending[0].resolve(envelope("a0"));
    await flush();
    expect(pending).toHaveLength(4);
    expect(pending[1].done).toBe(false);
    expect(pending[2].done).toBe(false);
    expect(view.summariesRef.current).toHaveProperty("a0");
    pending[3].resolve(envelope("a180"));
    await flush();
    expect(pending).toHaveLength(5);
    expect(pending.filter((row) => !row.done)).toHaveLength(3);
    expect(view.summariesRef.current).toHaveProperty("a180");
    view.cleanup?.();
    await flush();
    expect(pending.filter((row) => !row.done)).toHaveLength(0);
    expect(pending).toHaveLength(5);
  });

  it("692개 전체를 한 번씩 60개 이하로 보내며 완료 순서가 달라도 동시3을 넘지 않는다", async () => {
    const pending = setupFetch();
    const view = mount(692);
    let maxActive = 0;
    while (pending.some((row) => !row.done)) {
      maxActive = Math.max(maxActive, pending.filter((row) => !row.done).length);
      const row = [...pending].reverse().find((item) => !item.done)!;
      row.resolve(new Response(JSON.stringify({
        data: row.ids.map((location_id) => ({
          location_id, latest: [], alerts: [], measurement_point: null,
        })),
        meta: { request_id: "test", generated_at: "test", duration_ms: 0 },
      }), { headers: { "content-type": "application/json" } }));
      await flush();
      expect(pending.filter((item) => !item.done).length).toBeLessThanOrEqual(3);
    }
    expect(maxActive).toBe(3);
    expect(pending).toHaveLength(12);
    expect(pending.every((row) => row.ids.length <= 60)).toBe(true);
    const ids = pending.flatMap((row) => row.ids);
    expect(ids).toHaveLength(692);
    expect(new Set(ids).size).toBe(692);
    expect(Object.keys(view.summariesRef.current)).toHaveLength(692);
    view.cleanup?.();
  });

  it("HTTP 실패 chunk를 cache에 성공으로 넣거나 재시도하지 않고 다음 chunk를 처리한다", async () => {
    const pending = setupFetch();
    const view = mount(240);
    pending[0].resolve(new Response("Gateway", { status: 504 }));
    await flush();
    expect(pending).toHaveLength(4);
    expect(view.summariesRef.current).toEqual({});
    pending[3].resolve(envelope("a180"));
    await flush();
    expect(view.summariesRef.current).toHaveProperty("a180");
    expect(view.summariesRef.current).not.toHaveProperty("a0");
    expect(pending).toHaveLength(4);
    view.cleanup?.();
    await flush();
  });

  it("부분 성공 cache를 유지하면 겹치는 다음 viewport가 성공 ID를 다시 요청하지 않는다", async () => {
    const pending = setupFetch();
    const old = mount(240);
    pending[0].resolve(envelope("a0"));
    await flush();
    old.cleanup?.();
    await flush();
    const before = pending.length;
    const cleanup = effect(
      [{ location_id: "a0" }, { location_id: "a1" }],
      "map", 0, { current: 0 }, old.summariesRef,
      vi.fn(), getMarkerSummaries,
    );
    expect(pending).toHaveLength(before + 1);
    expect(pending[before].ids).toEqual(["a1"]);
    cleanup?.();
    await flush();
  });
});

describe("마커 모델 참조 보존", () => {
  function renderer() {
    if (!markerMemo) throw new Error("marker 모델의 useMemo가 필요합니다.");
    const render = new Function(
      "useMemo", "mapLocations", "selectedId", "summaries", "setSelectedId",
      "markerState", "markerTemperature", "markerCondition",
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
      (kind: string) => kind,
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
