import type { WeatherValue } from "@/lib/api";

export const DETAIL_METRICS = {
  temperature: ["T1H", "TMP", "TEMP", "temperature", "temperature_2m", "temp_c"],
  feels: ["FEELS_LIKE", "apparent_temperature", "feels_like", "feelslike_c"],
  humidity: ["REH", "HUMIDITY", "relative_humidity_2m", "humidity"],
  wind: ["WSD", "WIND_SPEED", "wind_speed", "wind_speed_10m", "wind_kph"],
  direction: ["VEC", "WIND_DIRECTION", "wind_direction", "wind_direction_10m", "wind_degree"],
  probability: ["POP", "PRECIP_PROB", "precipitation_probability", "chance_of_rain", "chance_of_snow"],
  precipitation: ["RN1", "PCP", "PRECIP", "precipitation", "precipitation_sum", "precip_mm"],
  min: ["TMN", "temperature_2m_min", "mintemp_c"],
  max: ["TMX", "temperature_2m_max", "maxtemp_c"],
  pm10: ["PM10", "pm10"],
  pm25: ["PM25", "pm25", "pm2_5"],
  sunrise: ["SUNRISE", "sunrise"],
  sunset: ["SUNSET", "sunset"],
  uv: ["UV", "UV_INDEX", "uv_index", "uv_index_max", "uv"],
} as const;
export function weatherMetric(values: WeatherValue[], keys: readonly string[]) {
  return values.filter(v => keys.includes(v.metric_key)).sort((a, b) =>
    Number(!["observed", "nowcast"].includes(a.forecast_style)) - Number(!["observed", "nowcast"].includes(b.forecast_style)) ||
    Date.parse(b.target_at) - Date.parse(a.target_at) || (b.issued_at ?? "").localeCompare(a.issued_at ?? "") || a.provider.localeCompare(b.provider))[0];
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
  const known = ["deg_c", "°C", "℃", "m/s", "m_s", "km/h", "kph", "km_h", "kmh", "%", "mm", "deg", "degrees", "μg/m³", "ug/m3", "index", ""];
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
    values: [...sources.values()].sort((a, b) => (Date.parse(b[0].issued_at ?? b[0].known_at ?? "") || 0) - (Date.parse(a[0].issued_at ?? a[0].known_at ?? "") || 0) || b.length - a.length || a[0].provider.localeCompare(b[0].provider))[0],
  }));
}
type Condition = { glyph: string; label: string };
type Slot = ReturnType<typeof hourlyWeather>[number];
const kst = (time: string | number) => new Date(new Date(time).getTime() + 9 * 3600000).toISOString();
const dayKey = (time: string | number) => kst(time).slice(0, 10);
const dateLabel = (time: string | number) => kst(time).slice(5, 10).replace("-", ".");
const hourLabel = (time: string) => `${Number(kst(time).slice(11, 13))}시`;

export function tomorrowWeather(forecast: WeatherValue[], reference: string | number) {
  const date = dayKey(new Date(reference).getTime() + 86400000);
  const rows = forecast.filter(row => Number.isFinite(Date.parse(row.target_at)) && dayKey(row.target_at) === date);
  // 하루 최저/최고는 제공된 일별 값만 쓴다. 부분 시간대의 최저를 하루 최저로 만들지 않는다.
  const slots = hourlyWeather(rows);
  const period = (start: number, end: number, preferred: number) => slots.filter(slot => {
    const hour = Number(kst(slot.time).slice(11, 13));
    return hour >= start && hour < end && slot.values.some(row => ["SKY", "PTY", "WEATHER_CODE", "weather_code"].includes(row.metric_key));
  }).sort((a, b) => Math.abs(Number(kst(a.time).slice(11, 13)) - preferred) - Math.abs(Number(kst(b.time).slice(11, 13)) - preferred))[0];
  return { date, rows, day: period(6, 18, 12), night: period(18, 24, 21) };
}

function clockValue(values: WeatherValue[], keys: readonly string[]) {
  const value = weatherMetric(values, keys)?.value_text;
  if (!value) return "미제공";
  if (/^\d{1,2}:\d{2}$/.test(value)) return value;
  return Number.isFinite(Date.parse(value)) ? kst(value).slice(11, 16) : value;
}

export function TemperatureGraph({ slots }: { slots: Slot[] }) {
  const temperatures = slots.map(slot => weatherMetric(slot.values, DETAIL_METRICS.temperature));
  const numbers = temperatures.map(row => row && ["deg_c", "°C", "℃"].includes(row.unit ?? "") && Number.isFinite(row.value_number) ? row.value_number : null);
  const valid = numbers.filter((n): n is number => n !== null);
  if (!valid.length) return <p className="weather-chart-empty">그래프로 표시할 기온 자료가 없습니다.</p>;
  const min = Math.min(...valid) - 2, max = Math.max(...valid) + 2;
  const y = (value: number) => 100 - (value - min) / (max - min) * 76;
  let path = "";
  numbers.forEach((value, i) => {
    if (value === null) return;
    const previous = i > 0 && numbers[i - 1] !== null && Date.parse(slots[i].time) - Date.parse(slots[i - 1].time) <= 2 * 3600000;
    path += `${previous ? "L" : "M"}${i * 64 + 32},${y(value)} `;
  });
  return <svg className="weather-temperature-graph" role="img" aria-label="시간별 기온 변화 그래프" viewBox={`0 0 ${slots.length * 64} 128`} width={slots.length * 64} height="128">
    {[30, 66, 102].map(y => <line key={y} x1="0" x2={slots.length * 64} y1={y} y2={y} className="weather-chart-grid"/>)}
    <path d={path.trim()} fill="none" className="weather-chart-line"/>
    {numbers.map((value, i) => value !== null ? <g key={slots[i].time}><circle cx={i * 64 + 32} cy={y(value)} r="3"><title>{`${dateLabel(slots[i].time)} ${hourLabel(slots[i].time)} · ${value}℃`}</title></circle><text x={i * 64 + 32} y={y(value) - 12} textAnchor="middle">{value}°</text></g> : null)}
  </svg>;
}

function WindDirection({ values }: { values: WeatherValue[] }) {
  const row = weatherMetric(values, DETAIL_METRICS.direction);
  const text = weatherDetailText(values, DETAIL_METRICS.direction, "°");
  if (row?.value_number === null || row?.value_number === undefined || !["deg", "degrees", "°"].includes(row.unit ?? "")) return <span>{text}</span>;
  return <span className="weather-wind-direction" role="img" aria-label={text} title={text}><span aria-hidden="true" style={{transform:`rotate(${row.value_number}deg)`}}>↓</span></span>;
}

export function WeatherDetails({ values, forecast, outlook = [], outlookPending = false, outlookMessage = "", referenceAt = Date.now(), condition }: {
  values: WeatherValue[]; forecast?: WeatherValue[]; outlook?: WeatherValue[]; outlookPending?: boolean; outlookMessage?: string;
  referenceAt?: string | number; condition: (rows: WeatherValue[]) => Condition;
}) {
  if (forecast) {
    const slots = hourlyWeather(forecast);
    if (!slots.length) return <p role="status">시간별 예보가 없습니다.</p>;
    return <section className="weather-hourly-section" aria-label="상세 시간별 예보"><h3>상세 시간별 예보</h3>
      <div className="weather-hourly" role="region" aria-label="시간별 상세 예보" tabIndex={0}>
        <table style={{width: 76 + slots.length * 64}}><caption className="sr-only">한국 시간 기준 기온 그래프와 강수·습도·바람 예보</caption>
          <colgroup><col style={{width:76}}/>{slots.map(slot => <col key={slot.time} style={{width:64}}/>)}</colgroup>
          <thead><tr><th scope="col">한국 시간</th>{slots.map(slot => <th scope="col" key={slot.time} title={`${slot.values[0]?.provider} · ${slot.values[0]?.issued_at ?? "발표 시각 미제공"}`}><time dateTime={slot.time}><small>{dateLabel(slot.time)}</small>{hourLabel(slot.time)}</time><span className="weather-hour-icon" title={condition(slot.values).label}>{condition(slot.values).glyph}</span><span className="sr-only">{condition(slot.values).label}</span></th>)}</tr></thead>
          <tbody><tr className="weather-chart-row"><th scope="row">기온<br/>℃</th><td colSpan={slots.length}><TemperatureGraph slots={slots}/></td></tr>{[
            ["강수확률", DETAIL_METRICS.probability, "%"], ["강수량", DETAIL_METRICS.precipitation, "mm"],
            ["습도", DETAIL_METRICS.humidity, "%"], ["풍속", DETAIL_METRICS.wind, "m/s"], ["풍향", DETAIL_METRICS.direction, "°"],
          ].map(([label, keys, suffix]) => <tr key={label as string}><th scope="row">{label}</th>{slots.map(slot => <td key={slot.time}>{keys === DETAIL_METRICS.direction ? <WindDirection values={slot.values}/> : weatherDetailText(slot.values, keys as readonly string[], suffix as string)}</td>)}</tr>)}</tbody>
        </table>
      </div><p className="weather-forecast-basis">한국 시간 · 예보 시각별 제공처·발표 기준은 열 제목에서 확인할 수 있습니다.</p>
    </section>;
  }
  const state = condition(values);
  const tomorrow = tomorrowWeather(outlook, referenceAt);
  const period = (label: string, slot: Slot | undefined) => <p className="weather-tomorrow-period"><span>{label}</span>{slot ? <><span aria-label={condition(slot.values).label}>{condition(slot.values).glyph} {condition(slot.values).label}</span><span>강수 {weatherDetailText(slot.values, DETAIL_METRICS.probability, "%")}</span></> : <span>미제공</span>}</p>;
  return <section className="weather-detail-overview" aria-label="현재 날씨 상세">
    <div className="weather-day-panels">
      <section className="weather-current-panel" aria-label="현재 날씨"><span className="weather-date-badge">현재 {dateLabel(referenceAt)}</span><div className="weather-current-hero"><span aria-hidden="true">{state.glyph}</span><strong>{weatherDetailText(values, DETAIL_METRICS.temperature, "°")}</strong></div><strong>{state.label}</strong><p>최저 {weatherDetailText(values, DETAIL_METRICS.min, "°")} · 최고 {weatherDetailText(values, DETAIL_METRICS.max, "°")}</p></section>
      <section className="weather-tomorrow-panel" aria-label="내일 날씨"><span className="weather-date-badge">내일 {tomorrow.date.slice(5).replace("-", ".")}</span>{outlookPending ? <p role="status">예보 조회 중…</p> : outlookMessage ? <p role="status">{outlookMessage}</p> : !tomorrow.rows.length ? <p>내일 예보 미제공</p> : <><div className="weather-tomorrow-range"><span><small>최저</small><strong>{weatherDetailText(tomorrow.rows, DETAIL_METRICS.min, "°")}</strong></span><span aria-hidden="true">/</span><span><small>최고</small><strong>{weatherDetailText(tomorrow.rows, DETAIL_METRICS.max, "°")}</strong></span></div>{period("낮", tomorrow.day)}{period("밤", tomorrow.night)}</>}</section>
    </div>
    <section className="weather-detail-metrics" aria-label="세부 날씨 정보"><h3 className="sr-only">미세먼지와 세부 날씨 정보</h3><div className="metric-grid">{[
      ["미세먼지", DETAIL_METRICS.pm10, "μg/m³"], ["초미세먼지", DETAIL_METRICS.pm25, "μg/m³"],
      ["체감", DETAIL_METRICS.feels, "°"], ["풍속", DETAIL_METRICS.wind, "m/s"], ["풍향", DETAIL_METRICS.direction, "°"],
      ["습도", DETAIL_METRICS.humidity, "%"], ["자외선", DETAIL_METRICS.uv, ""],
    ].map(([label, keys, suffix]) => <div className="metric-card" key={label as string}><span>{label}</span><strong>{weatherDetailText(values, keys as readonly string[], suffix as string)}</strong></div>)}<div className="metric-card weather-sun-times"><span>일출 <strong>{clockValue(values, DETAIL_METRICS.sunrise)}</strong></span><span>일몰 <strong>{clockValue(values, DETAIL_METRICS.sunset)}</strong></span></div></div></section>
  </section>;
}
