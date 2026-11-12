import { describe, expect, it } from "vitest";
import { tooltipValueText } from "./echarts-tooltip";

describe("툴팁 수치 정밀도", () => {
  it.each([0, -1, 0.0001, -0.0001, 12.3456789])("%s의 원래 수치를 반올림하지 않는다", value => {
    expect(tooltipValueText(value)).toBe(String(value));
  });
  it("미제공을 0으로 표시하지 않는다", () => {
    expect(tooltipValueText(null)).toBe("");
    expect(tooltipValueText(undefined)).toBe("");
  });
});
