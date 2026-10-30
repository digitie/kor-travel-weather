import type { WeatherValue } from "@/lib/api";

export const DETAIL_METRICS = {
  temperature: ["T1H", "TMP", "TEMP", "temperature", "temperature_2m", "temp_c"],
  feels: ["FEELS_LIKE", "apparent_temperature", "feels_like", "feelslike_c"],
  humidity: ["REH", "HUMIDITY", "relative_humidity_2m", "humidity"],
  wind: ["WSD", "WIND_SPEED", "wind_speed", "wind_speed_10m", "wind_kph"],
  direction: ["VEC", "WIND_DIRECTION", "wind_direction", "wind_direction_10m", "wind_degree"],
  probability: ["POP", "precipitation_probability", "chance_of_rain", "chance_of_snow"],
  precipitation: ["RN1", "PCP", "PRECIP", "precipitation", "precipitation_sum", "precip_mm"],
  min: ["TMN", "temperature_2m_min", "mintemp_c"],
  max: ["TMX", "temperature_2m_max", "maxtemp_c"],
  pm10: ["PM10", "pm10"],
  pm25: ["PM25", "pm25", "pm2_5"],
  uv: ["UV", "uv_index", "uv_index_max", "uv"],
} as const;
export function weatherMetric(values: WeatherValue[], keys: readonly string[]) {
  return values.filter(v => keys.includes(v.metric_key)).sort((a, b) =>
    b.target_at.localeCompare(a.target_at) || (b.issued_at ?? "").localeCompare(a.issued_at ?? "") || a.provider.localeCompare(b.provider))[0];
}
export function weatherDetailText(values: WeatherValue[], keys: readonly string[], suffix: string) {
  const row = weatherMetric(values, keys);
  if (!row) return "미제공";
  let value = row.value_number;
  if (value !== null && keys === DETAIL_METRICS.wind && ["km/h", "kph", "km_h", "kmh"].includes(row.unit ?? "")) value = Math.round(value / 3.6 * 10) / 10;
  if (value !== null && keys === DETAIL_METRICS.direction) {
    const names = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"];
    return `${names[Math.round(((value % 360 + 360) % 360) / 45) % 8]}풍 ${value}°`;
  }
  const rawUnit = row.unit ?? "";
  const known = ["deg_c", "°C", "℃", "m/s", "m_s", "km/h", "kph", "km_h", "kmh", "%", "mm", "deg", "degrees", "μg/m³", "ug/m3", ""];
  const unit = !rawUnit ? "" : known.includes(rawUnit) ? suffix : ` ${rawUnit}`;
  const number = value !== null && ["deg_c", "°C", "℃", "m/s", "m_s"].includes(rawUnit)
    ? (Number(value.toLocaleString("ko-KR", {maximumFractionDigits:1,useGrouping:false})) === 0 && value !== 0 ? value : value.toLocaleString("ko-KR", {maximumFractionDigits:1,useGrouping:false})) : value;
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
    const group = sources.get(key) ?? []; group.push(row); sources.set(key, group); slots.set(instant, sources);
  }
  return [...slots].sort(([a], [b]) => a.localeCompare(b)).slice(0, 24).map(([time, sources]) => ({ time,
    values: [...sources.values()].sort((a, b) => b.length - a.length || (b[0].issued_at ?? "").localeCompare(a[0].issued_at ?? "") || a[0].provider.localeCompare(b[0].provider))[0],
  }));
}
type Condition = { glyph: string; label: string };
export function WeatherDetails({ values, forecast, condition }: { values: WeatherValue[]; forecast?: WeatherValue[]; condition: (rows: WeatherValue[]) => Condition }) {
  if (forecast) return <div className="weather-hourly" role="region" aria-label="시간별 상세 예보" tabIndex={0}><table><thead><tr><th scope="col">한국 시간</th>{hourlyWeather(forecast).map(slot => <th scope="col" key={slot.time} title={`${slot.values[0]?.provider} · ${slot.values[0]?.issued_at ?? "발표 시각 미제공"}`}><time dateTime={slot.time}>{`${new Date(Date.parse(slot.time) + 9 * 3600000).toISOString().slice(5, 10).replace("-", ".")} ${Number(new Date(Date.parse(slot.time) + 9 * 3600000).toISOString().slice(11, 13))}시`}</time><div title={condition(slot.values).label}>{condition(slot.values).glyph} {condition(slot.values).label}</div></th>)}</tr></thead><tbody>{[
    ["기온", DETAIL_METRICS.temperature, "°"], ["강수확률", DETAIL_METRICS.probability, "%"],
    ["강수량", DETAIL_METRICS.precipitation, "mm"], ["습도", DETAIL_METRICS.humidity, "%"],
    ["풍속", DETAIL_METRICS.wind, "m/s"], ["풍향", DETAIL_METRICS.direction, "°"],
  ].map(([label, keys, suffix]) => <tr key={label as string}><th scope="row">{label}</th>{hourlyWeather(forecast).map(slot => <td key={slot.time}>{weatherDetailText(slot.values, keys as readonly string[], suffix as string)}</td>)}</tr>)}</tbody></table></div>;
  const state = condition(values);
  return <section className="weather-detail-overview" aria-label="현재 날씨 상세"><div className="weather-current-hero"><span>{state.glyph}</span><strong>{weatherDetailText(values, DETAIL_METRICS.temperature, "°")}</strong><span>{state.label}</span></div><p>최저 {weatherDetailText(values, DETAIL_METRICS.min, "°")} · 최고 {weatherDetailText(values, DETAIL_METRICS.max, "°")}</p><div className="metric-grid">{[
    ["체감", DETAIL_METRICS.feels, "°"], ["풍속", DETAIL_METRICS.wind, "m/s"], ["풍향", DETAIL_METRICS.direction, "°"],
    ["습도", DETAIL_METRICS.humidity, "%"], ["자외선", DETAIL_METRICS.uv, ""], ["미세먼지", DETAIL_METRICS.pm10, "μg/m³"], ["초미세먼지", DETAIL_METRICS.pm25, "μg/m³"],
  ].map(([label, keys, suffix]) => <div className="metric-card" key={label as string}><span>{label}</span><strong>{weatherDetailText(values, keys as readonly string[], suffix as string)}</strong></div>)}</div></section>;
}
