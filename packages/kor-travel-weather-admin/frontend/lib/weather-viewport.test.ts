import { describe, expect, it } from "vitest";

import { estimateViewport, viewportCenter } from "./weather-viewport";

describe("지도 load 전 화면 범위 추정(B7)", () => {
  it("첫 카메라와 컨테이너 크기로 MapLibre와 같은 Mercator 범위를 계산한다", () => {
    const viewport = estimateViewport([127, 37], 7, 1000, 800)!;
    // zoom 7의 세계 너비 512*2^7 px에서 1000px은 경도 약 5.49도다.
    expect(viewport.east - viewport.west).toBeCloseTo((1000 / (512 * 128)) * 360, 6);
    expect((viewport.east + viewport.west) / 2).toBeCloseTo(127, 6);
    expect(viewport.north).toBeGreaterThan(37);
    expect(viewport.south).toBeLessThan(37);
    // Mercator라 북쪽 폭이 남쪽 폭보다 좁다.
    expect(viewport.north - 37).toBeLessThan(37 - viewport.south);
    const center = viewportCenter(viewport)!;
    expect(center[0]).toBeCloseTo(127, 6);
    expect(Math.abs(center[1] - 37)).toBeLessThan(0.1);
  });
  it("크기를 모르거나 0이면 추정하지 않는다", () => {
    expect(estimateViewport([127, 37], 7, 0, 800)).toBeNull();
    expect(estimateViewport([127, 37], Number.NaN, 100, 100)).toBeNull();
    expect(viewportCenter(null)).toBeNull();
  });
});
