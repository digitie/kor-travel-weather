"use client";

import { EChartsLineChart } from "./evilcharts/charts/echarts-line-chart";
import type { ChartConfig } from "./evilcharts/ui/echarts-chart";
import type { TemperatureChartModel } from "@/lib/weather-temperature-chart";

export default function WeatherTemperatureChart({ model }: { model: TemperatureChartModel }) {
  const config: ChartConfig = Object.fromEntries(model.segments.map(key => [key, {
    label: "기온 (℃)", colors: { light: ["var(--brand)"], dark: ["var(--brand)"] },
  }]));
  return <EChartsLineChart data={model.data} config={config} xDataKey="time"
    className="weather-temperature-echarts" animation={false} curveType="linear">
    <EChartsLineChart.Grid />
    <EChartsLineChart.XAxis tickFormatter={value => value.slice(11)} />
    <EChartsLineChart.YAxis tickFormatter={value => `${value}°`} />
    <EChartsLineChart.Tooltip />
    {model.segments.map(key => <EChartsLineChart.Line key={key} dataKey={key} connectNulls={false} strokeWidth={2}>
      <EChartsLineChart.Dot /><EChartsLineChart.ActiveDot />
    </EChartsLineChart.Line>)}
  </EChartsLineChart>;
}
