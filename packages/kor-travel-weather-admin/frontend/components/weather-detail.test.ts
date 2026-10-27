import { readFileSync } from "node:fs";
import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";

import { forecastWindowStart, getForecast, getLatest } from "../lib/api";

// 실제 상세 effect를 실행해 지연·실패·cleanup 응답 순서 역전을 검증한다.
const source = readFileSync(new URL("./weather-map.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("weather-map.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback: ts.Expression | undefined;
// effect가 쓰는 모듈 수준 helper(현재 값 종류 판정)를 원본 그대로 함께 실행한다.
const helpers: string[] = [];
function visit(node: ts.Node) {
  if ((ts.isVariableStatement(node) && node.declarationList.declarations.some(
    (declaration) => declaration.name.getText(tree) === "CURRENT_KINDS",
  )) || (ts.isFunctionDeclaration(node) && node.name?.getText(tree) === "includesCurrentKind")) {
    helpers.push(node.getText(tree));
  }
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect"
    && node.arguments[1]?.getText(tree) === "[refreshToken, selectedLocationId, providerFilter, kindFilter]") callback = node.arguments[0];
  ts.forEachChild(node, visit);
}
visit(tree);
if (!callback || helpers.length !== 2) throw new Error("상세 effect를 찾지 못했습니다.");
const setters = ["setValues", "setForecast", "setMessage", "setForecastMessage",
  "setLastRefreshedAt", "setLoading", "setForecastLoading"];
const effect = new Function("selectedLocationId", "providerFilter", ...setters, "getLatest", "getForecast", "forecastWindowStart", "kindFilter",
  ts.transpileModule(helpers.join("\n") + "\nreturn (" + callback.getText(tree) + ")();", {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText,
) as (...args: unknown[]) => () => void;

type Pending = { url: string; signal: AbortSignal; resolve: (response: Response) => void };
function setup() {
  const requests: Pending[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, options: RequestInit) => new Promise<Response>((resolve) => {
    requests.push({ url, signal: options.signal as AbortSignal, resolve });
  })));
  const state: Record<string, unknown> = {};
  const mount = (id: string | undefined) => effect(id, undefined,
    ...setters.map((key) => (value: unknown) => { state[key] = value; }),
    getLatest, getForecast, forecastWindowStart);
  return { requests, state, mount };
}
function respond(request: Pending, data: unknown[], status = 200) {
  request.resolve(new Response(JSON.stringify({ data, detail: "조회 실패" }), {
    status, headers: { "content-type": "application/json" },
  }));
}
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
afterEach(() => vi.unstubAllGlobals());

describe("선택 위치의 독립 상세 로딩", () => {
  it("예보가 멈춰도 최신값을 먼저 표시하고, 예보 실패가 최신값을 지우지 않는다", async () => {
    const { requests, state, mount } = setup();
    mount("one");
    expect(requests).toHaveLength(2);
    expect(requests[0].url).toContain("/one/latest");
    respond(requests[0], [{ value_id: "current" }]);
    await flush();
    expect(state.setValues).toEqual([{ value_id: "current" }]);
    expect(state.setLoading).toBe(false);
    expect(state.setForecastLoading).toBe(true);
    respond(requests[1], [], 503);
    await flush();
    expect(state.setForecastLoading).toBe(false);
    expect(state.setForecastMessage).toContain("조회 실패");
    expect(state.setValues).toEqual([{ value_id: "current" }]);
  });

  it("최신값이 실패해도 먼저 도착한 예보는 유지한다", async () => {
    const { requests, state, mount } = setup();
    mount("one");
    respond(requests[1], [{ value_id: "forecast" }]);
    await flush();
    expect(state.setForecastLoading).toBe(false);
    expect(state.setLoading).toBe(true);
    respond(requests[0], [], 503);
    await flush();
    expect(state.setMessage).toContain("조회 실패");
    expect(state.setForecast).toEqual([{ value_id: "forecast" }]);
  });

  it("연속 선택·새로고침은 이전 요청 둘을 취소하고 늦은 응답을 무시한다", async () => {
    const { requests, state, mount } = setup();
    let cleanup = mount("one");
    for (let n = 0; n < 8; n++) { cleanup(); cleanup = mount(n === 7 ? "one" : `next-${n}`); }
    expect(requests.slice(0, -2).every((request) => request.signal.aborted)).toBe(true);
    expect(requests.slice(-2).every((request) => !request.signal.aborted)).toBe(true);
    respond(requests.at(-2)!, [{ value_id: "new" }]);
    respond(requests.at(-1)!, []);
    await flush();
    for (const request of requests.slice(0, -2)) respond(request, [{ value_id: "stale" }]);
    await flush();
    expect(state.setValues).toEqual([{ value_id: "new" }]);
    expect(state.setForecast).toEqual([]);
    expect(state.setMessage).toBe("");
    expect(state.setForecastMessage).toBe("");
    cleanup();
  });

  it("검색 결과가 없으면 요청 없이 두 loading을 종료한다", () => {
    const { requests, state, mount } = setup();
    mount(undefined)();
    expect(requests).toEqual([]);
    expect(state.setLoading).toBe(false);
    expect(state.setForecastLoading).toBe(false);
    expect(state.setValues).toEqual([]);
  });
});
