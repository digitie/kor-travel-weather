import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SourceMultiSelect } from "./source-multi-select";

const options = [
  { value: "python-kma-api", label: "기상청" },
  { value: "open_meteo", label: "Open-Meteo" },
  { value: "weatherapi", label: "WeatherAPI" },
];

const cases: Array<[string[] | null, string]> = [
  [null, "모든 소스"],
  [["python-kma-api", "open_meteo"], "2개 소스"],
  [["open_meteo"], "Open-Meteo"],
  [[], "소스 선택"],
];

describe("소스 필터 접근성 이름", () => {
  it.each(cases)("보이는 선택 %j를 이름에도 포함한다", (value, label) => {
    const html = renderToStaticMarkup(createElement(SourceMultiSelect, {
      id: "weather-source-filter", options, value, onChange: () => undefined,
    }));
    expect(html).toContain(`aria-label="날씨 소스 필터: ${label}"`);
    expect(html).toContain(`<span>${label}</span>`);
  });
});
