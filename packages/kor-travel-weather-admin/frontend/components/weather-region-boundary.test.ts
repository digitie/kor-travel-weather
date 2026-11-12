import { readFileSync } from "node:fs";
import ts from "typescript";
import { describe, expect, it, vi } from "vitest";

const source = readFileSync(new URL("./weather-region-boundary.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("boundary.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback: ts.Expression | undefined;
function visit(node: ts.Node) {
  if (ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect") callback = node.arguments[0];
  ts.forEachChild(node, visit);
}
visit(tree);
if (!callback) throw new Error("실제 지도 effect가 필요합니다.");
const effect = ts.transpileModule(`const effect = ${callback.getText(tree)}; return effect;`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
function fixture(width: number, fetcher: ReturnType<typeof vi.fn>) {
  let observedWidth = width;
  const listeners = new Map<string, (event: object) => void>();
  const layers = new Set<string>(); let hasSource = false;
  const stage = { setAttribute: vi.fn(), removeAttribute: vi.fn(), querySelector: () => ({ getBoundingClientRect: () => ({ bottom: 340, left: width - 350 }) }) };
  const map = {
    on: vi.fn((event: string, fn: (event: object) => void) => listeners.set(event, fn)),
    off: vi.fn((event: string) => listeners.delete(event)),
    getLayer: (id: string) => layers.has(id), removeLayer: vi.fn((id: string) => layers.delete(id)),
    getSource: () => hasSource, removeSource: vi.fn(() => { hasSource = false; }),
    addSource: vi.fn(() => { hasSource = true; }), addLayer: vi.fn((layer: { id: string }) => layers.add(layer.id)),
    isStyleLoaded: () => true, getStyle: () => ({ layers: [] }),
    getContainer: () => ({ getBoundingClientRect: () => ({ width: observedWidth, height: 800, top: 10, right: observedWidth }), closest: () => stage }), fitBounds: vi.fn(),
  };
  const message = vi.fn();
  const run = new Function("map", "request", "id", "setMessage", "fetch", effect)(map, { latitude: 37, longitude: 127, level: "emd" }, "boundary", message, fetcher);
  return { map, stage, listeners, message, resize: (next: number) => { observedWidth = next; listeners.get("resize")?.({}); }, cleanup: run() as () => void };
}
const boundary = { name: "테스트동", bbox: [127, 37, 127.1, 37.1], feature: { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [] } } };
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); };
describe("지역 경계 지도 생명주기", () => {
  it.each([1440, 700, 375])("폭%s에서 실제 경계를 표시하고 상세 패널 여백을 남긴다", async width => {
    const current = fixture(width, vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: boundary }))));
    await flush();
    expect(current.map.addSource).toHaveBeenCalledWith("boundary", { type: "geojson", data: boundary.feature });
    expect(current.map.fitBounds).toHaveBeenCalledOnce();
    const options = current.map.fitBounds.mock.calls[0][1];
    expect(width <= 700 ? options.padding.top : options.padding.right).toBeGreaterThan(200);
    expect(current.stage.setAttribute).toHaveBeenCalledWith("data-weather-boundary-compact", String(width <= 700));
    current.resize(width <= 700 ? 1440 : 375);
    expect(current.stage.setAttribute).toHaveBeenLastCalledWith("data-weather-boundary-compact", String(width > 700));
    expect(current.map.fitBounds).toHaveBeenCalledOnce();
    current.listeners.get("style.load")?.({});
    expect(current.map.fitBounds).toHaveBeenCalledOnce();
    current.cleanup(); expect(current.map.removeSource).toHaveBeenCalled(); expect(current.listeners.size).toBe(0);
    expect(current.stage.removeAttribute).toHaveBeenCalledWith("data-weather-boundary-compact");
  });
  it("늦은 응답은 새 선택을 이동시키지 않고 사용자의 수동 이동도 덮지 않는다", async () => {
    let resolve!: (response: Response) => void;
    const fetcher = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(() => new Promise<Response>(done => { resolve = done; }));
    const old = fixture(1440, fetcher);
    old.cleanup(); resolve(new Response(JSON.stringify({ data: boundary }))); await flush();
    expect(fetcher.mock.calls[0][1].signal?.aborted).toBe(true);
    expect(old.map.addSource).not.toHaveBeenCalled(); expect(old.map.fitBounds).not.toHaveBeenCalled();
    const current = fixture(375, vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: boundary }))));
    current.listeners.get("movestart")?.({ originalEvent: {} }); await flush();
    expect(current.map.addSource).toHaveBeenCalled(); expect(current.map.fitBounds).not.toHaveBeenCalled(); current.cleanup();
  });
});
