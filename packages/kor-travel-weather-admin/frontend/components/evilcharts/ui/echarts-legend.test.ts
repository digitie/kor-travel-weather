import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { LegendOverlay } from "./echarts-legend";

const props = { seriesKeys: ["current", "forecast"], config: { current: { label: "현재", color: "blue" }, forecast: { label: "예보", color: "red" } },
  variant: "square" as const, align: "left" as const, verticalAlign: "bottom" as const,
  selectedKey: null, hoveredKey: null, isClickable: true, onToggle: vi.fn(), style: {} };
it("선택 가능한 범례는 네이티브 button이며 전체 선택 상태도 표현한다", () => {
  const html = renderToStaticMarkup(createElement(LegendOverlay, props));
  expect(html.match(/<button/gu)).toHaveLength(2);
  expect(html.match(/aria-pressed="true"/gu)).toHaveLength(2);
  expect(html.match(/type="button"/gu)).toHaveLength(2);
  const root = LegendOverlay(props);
  const first = (root.props.children as ReactElement<{ onClick: () => void }>[])[0];
  first.props.onClick(); expect(props.onToggle).toHaveBeenCalledWith("current");
});
it("선택하지 않은 범례의 상태와 정적 범례의 비상호작용을 구분한다", () => {
  const selected = renderToStaticMarkup(createElement(LegendOverlay, { ...props, selectedKey: "forecast" }));
  expect(selected.match(/aria-pressed="false"/gu)).toHaveLength(1);
  const fixed = renderToStaticMarkup(createElement(LegendOverlay, { ...props, isClickable: false }));
  expect(fixed).not.toContain("<button"); expect(fixed).toContain("현재"); expect(fixed).toContain("예보");
});
