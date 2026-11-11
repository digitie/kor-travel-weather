import type { WeatherValue } from "./api";

export const DETAIL_METRICS = {
  temperature: [
    "T1H",
    "TMP",
    "TEMP",
    "temperature",
    "temperature_2m",
    "temp_c",
  ],
  feels: ["FEELS_LIKE", "apparent_temperature", "feels_like", "feelslike_c"],
  humidity: ["REH", "HUMIDITY", "relative_humidity_2m", "humidity"],
  wind: ["WSD", "WIND_SPEED", "wind_speed", "wind_speed_10m", "wind_kph"],
  direction: [
    "VEC",
    "WIND_DIRECTION",
    "wind_direction",
    "wind_direction_10m",
    "wind_degree",
  ],
  probability: [
    "POP",
    "PRECIP_PROB",
    "precipitation_probability",
    "chance_of_rain",
    "chance_of_snow",
  ],
  precipitation: [
    "RN1",
    "PCP",
    "PRECIP",
    "precipitation",
    "precipitation_sum",
    "precip_mm",
  ],
  min: ["TMN", "temperature_2m_min", "mintemp_c"],
  max: ["TMX", "temperature_2m_max", "maxtemp_c"],
  pm10: ["PM10", "pm10"],
  pm25: ["PM25", "pm25", "pm2_5"],
  sunrise: ["SUNRISE", "sunrise"],
  sunset: ["SUNSET", "sunset"],
  uv: ["UV", "UV_INDEX", "uv_index", "uv_index_max", "uv"],
} as const;
export function weatherMetric(values: WeatherValue[], keys: readonly string[]) {
  const metricKeys = new Set(keys);
  return values
    .filter((v) => metricKeys.has(v.metric_key))
    .sort(
      (a, b) =>
        Number(!["observed", "nowcast"].includes(a.forecast_style)) -
          Number(!["observed", "nowcast"].includes(b.forecast_style)) ||
        Date.parse(b.target_at) - Date.parse(a.target_at) ||
        (b.issued_at ?? "").localeCompare(a.issued_at ?? "") ||
        a.provider.localeCompare(b.provider),
    )[0];
}
export function weatherDetailText(
  values: WeatherValue[],
  keys: readonly string[],
  suffix: string,
) {
  const row = weatherMetric(values, keys);
  if (!row) return "미제공";
  let value = row.value_number;
  if (
    value !== null &&
    keys === DETAIL_METRICS.wind &&
    ["km/h", "kph", "km_h", "kmh"].includes(row.unit ?? "")
  )
    value = Math.round((value / 3.6) * 10) / 10;
  if (value !== null && keys === DETAIL_METRICS.direction) {
    const names = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"];
    return `${names[Math.round((((value % 360) + 360) % 360) / 45) % 8]}풍 ${value}°`;
  }
  const rawUnit = row.unit ?? "";
  const known = [
    "deg_c",
    "°C",
    "℃",
    "m/s",
    "m_s",
    "km/h",
    "kph",
    "km_h",
    "kmh",
    "%",
    "mm",
    "deg",
    "degrees",
    "μg/m³",
    "ug/m3",
    "index",
    "",
  ];
  const unit = !rawUnit ? "" : known.includes(rawUnit) ? suffix : ` ${rawUnit}`;
  const number =
    value !== null && ["deg_c", "°C", "℃", "m/s", "m_s"].includes(rawUnit)
      ? Number(
          value.toLocaleString("ko-KR", {
            maximumFractionDigits: 1,
            useGrouping: false,
          }),
        ) === 0 && value !== 0
        ? value
        : value.toLocaleString("ko-KR", {
            maximumFractionDigits: 1,
            useGrouping: false,
          })
      : value;
  return `${number ?? row.value_text ?? "미제공"}${unit}`;
}
/** 발표 단위·제공처를 섞지 않고 각 시각의 한 예보 묶음을 사용한다. */
export function hourlyWeather(values: WeatherValue[]) {
  const slots = new Map<string, Map<string, WeatherValue[]>>();
  for (const row of values) {
    if (!Number.isFinite(Date.parse(row.target_at))) continue;
    const instant = new Date(row.target_at).toISOString();
    const sources = slots.get(instant) ?? new Map<string, WeatherValue[]>();
    const key = `${row.provider}:${row.dataset_key}:${row.issued_at}`;
    const group = sources.get(key) ?? [];
    group.push(row);
    sources.set(key, group);
    slots.set(instant, sources);
  }
  return [...slots]
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(0, 24)
    .map(([time, sources]) => ({
      time,
      values: [...sources.values()].sort(
        (a, b) =>
          (Date.parse(b[0].issued_at ?? b[0].known_at ?? "") || 0) -
            (Date.parse(a[0].issued_at ?? a[0].known_at ?? "") || 0) ||
          b.length - a.length ||
          a[0].provider.localeCompare(b[0].provider),
      )[0],
    }));
}
export const kst = (time: string | number) =>
  new Date(new Date(time).getTime() + 9 * 3600000).toISOString();
const dayKey = (time: string | number) => kst(time).slice(0, 10);
export const dateLabel = (time: string | number) =>
  kst(time).slice(5, 10).replace("-", ".");
export const hourLabel = (time: string) =>
  `${Number(kst(time).slice(11, 13))}시`;

export function tomorrowWeather(
  forecast: WeatherValue[],
  reference: string | number,
) {
  const date = dayKey(new Date(reference).getTime() + 86400000);
  const rows = forecast.filter(
    (row) =>
      Number.isFinite(Date.parse(row.target_at)) &&
      dayKey(row.target_at) === date,
  );
  // 하루 최저/최고는 제공된 일별 값만 쓴다. 부분 시간대의 최저를 하루 최저로 만들지 않는다.
  const slots = hourlyWeather(rows);
  const period = (start: number, end: number, preferred: number) =>
    slots
      .filter((slot) => {
        const hour = Number(kst(slot.time).slice(11, 13));
        return (
          hour >= start &&
          hour < end &&
          slot.values.some((row) =>
            ["SKY", "PTY", "WEATHER_CODE", "weather_code"].includes(
              row.metric_key,
            ),
          )
        );
      })
      .sort(
        (a, b) =>
          Math.abs(Number(kst(a.time).slice(11, 13)) - preferred) -
          Math.abs(Number(kst(b.time).slice(11, 13)) - preferred),
      )[0];
  return { date, rows, day: period(6, 18, 12), night: period(18, 24, 21) };
}
