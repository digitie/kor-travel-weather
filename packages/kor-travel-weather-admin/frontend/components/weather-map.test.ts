import { readFileSync } from "node:fs";

import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getMarkerSummaries } from "../lib/api";

// 실제 effect callback을 실행한다. DOM/지도 대신 React의 cleanup→새 effect 순서만 명시한다.
const source = readFileSync(new URL("./weather-map.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("weather-map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback: ts.ArrowFunction | undefined;
const constants: string[] = [];
function visit(node: ts.Node) {
  if (ts.isVariableStatement(node) && node.declarationList.declarations.some(
    (declaration) => ["MARKER_BATCH_SIZE", "MARKER_BATCH_CONCURRENCY"].includes(declaration.name.getText(tree)),
  )) constants.push(node.getText(tree));
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
  signal: AbortSignal | undefined;
  resolve: (response: Response) => void;
  done: boolean;
};
function setupFetch() {
  const pending: Pending[] = [];
  vi.stubGlobal("fetch", vi.fn((_url: string, options: RequestInit) => new Promise<Response>(
    (resolve, reject) => {
      const row: Pending = {
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
    expect(current.setSummaries).toHaveBeenCalledTimes(1);
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

  it("일부 응답 뒤 cleanup해도 늦은 결과가 cache나 화면을 덮지 않는다", async () => {
    const pending = setupFetch();
    const view = mount();
    pending[0].resolve(envelope());
    await flush();
    view.cleanup?.();
    await flush();
    expect(pending.filter((row) => !row.done)).toHaveLength(0);
    expect(view.setSummaries).not.toHaveBeenCalled();
    expect(view.summariesRef.current).toEqual({});
    expect(pending).toHaveLength(3);
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
    expect(view.setSummaries).toHaveBeenCalledTimes(1);
    view.cleanup?.();
  });
});
