import type { WeatherValue } from "./api";
import { DETAIL_METRICS, kst, weatherMetric } from "./weather-detail-model";

type Slot = { time: string; values: WeatherValue[] };
export type TemperatureChartModel = {
  data: Record<string, string | number | null>[];
  segments: string[];
};

/** 누락·잘못된 단위·2시간을 넘는 간격을 선으로 이어 보이지 않는다. 0℃도 유효하다. */
export function temperatureChartModel(slots: Slot[]): TemperatureChartModel {
  const data: TemperatureChartModel["data"] = [];
  const segments: string[] = [];
  let active: string | null = null;
  let previous: number | null = null;
  for (const slot of slots) {
    const row = weatherMetric(slot.values, DETAIL_METRICS.temperature);
    const time = Date.parse(slot.time);
    const value = row && ["deg_c", "°C", "℃"].includes(row.unit ?? "")
      && typeof row.value_number === "number" && Number.isFinite(row.value_number)
      && Number.isFinite(time) ? row.value_number : null;
    const point: Record<string, string | number | null> = { time: Number.isFinite(time) ? kst(slot.time).slice(0, 16).replace("T", " ") : "시각 미제공" };
    if (value === null) { active = null; previous = null; }
    else {
      if (!active || previous === null || time - previous > 2 * 3600000 || time <= previous) {
        active = `temperature_${segments.length}`; segments.push(active);
      }
      point[active] = value;
      previous = time;
    }
    data.push(point);
  }
  return { data, segments };
}
