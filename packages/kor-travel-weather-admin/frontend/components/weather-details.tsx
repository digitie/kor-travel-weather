"use client";

import { VirtualTable, type ColumnDef } from "@kor-travel/ui/virtual-table";
import dynamic from "next/dynamic";
import { temperatureChartModel } from "@/lib/weather-temperature-chart";
import {
  DETAIL_METRICS,
  weatherMetric,
  weatherDetailText,
  hourlyWeather,
  tomorrowWeather,
  kst,
  dateLabel,
  hourLabel,
} from "@/lib/weather-detail-model";
import type { WeatherValue } from "@/lib/api";

type Condition = { glyph: string; label: string };
type Slot = ReturnType<typeof hourlyWeather>[number];
const WeatherTemperatureChart = dynamic(() => import("./weather-temperature-chart"), {
  ssr: false,
  loading: () => <p className="weather-chart-loading" role="status">기온 그래프 준비 중…</p>,
});
function clockValue(values: WeatherValue[], keys: readonly string[]) {
  const value = weatherMetric(values, keys)?.value_text;
  if (!value) return "미제공";
  if (/^\d{1,2}:\d{2}$/.test(value)) return value;
  return Number.isFinite(Date.parse(value)) ? kst(value).slice(11, 16) : value;
}

export function TemperatureGraph({ slots }: { slots: Slot[] }) {
  const model = temperatureChartModel(slots);
  if (!model.segments.length)
    return (
      <p className="weather-chart-empty">
        그래프로 표시할 기온 자료가 없습니다.
      </p>
    );
  return (
    <div
      className="weather-temperature-graph"
      role="img"
      aria-label="시간별 기온 변화 그래프"
    >
      <WeatherTemperatureChart model={model} />
    </div>
  );
}

function WindDirection({ values }: { values: WeatherValue[] }) {
  const row = weatherMetric(values, DETAIL_METRICS.direction);
  const text = weatherDetailText(values, DETAIL_METRICS.direction, "°");
  if (
    row?.value_number === null ||
    row?.value_number === undefined ||
    !["deg", "degrees", "°"].includes(row.unit ?? "")
  )
    return <span>{text}</span>;
  return (
    <span
      className="weather-wind-direction"
      role="img"
      aria-label={text}
      title={text}
    >
      <span
        aria-hidden="true"
        style={{ transform: `rotate(${row.value_number}deg)` }}
      >
        ↓
      </span>
    </span>
  );
}

export function WeatherDetails({
  values,
  forecast,
  outlook = [],
  outlookPending = false,
  outlookMessage = "",
  referenceAt = Date.now(),
  condition,
}: {
  values: WeatherValue[];
  forecast?: WeatherValue[];
  outlook?: WeatherValue[];
  outlookPending?: boolean;
  outlookMessage?: string;
  referenceAt?: string | number;
  condition: (rows: WeatherValue[]) => Condition;
}) {
  if (forecast) {
    const slots = hourlyWeather(forecast);
    if (!slots.length) return <p role="status">시간별 예보가 없습니다.</p>;
    return (
      <section className="weather-hourly-section" aria-label="상세 시간별 예보">
        <h3>상세 시간별 예보</h3>
        <div
          className="weather-hourly"
          role="region"
          aria-label="시간별 상세 예보"
          tabIndex={0}
        >
          <div style={{ width: 76 + slots.length * 64 }}>
            <div style={{ marginLeft: 76 }}>
              <TemperatureGraph slots={slots} />
            </div>
            <VirtualTable<{
              label: string;
              keys: readonly string[];
              suffix: string;
            }>
              ariaLabel="한국 시간 기준 기온과 강수·습도·바람 예보"
              getRowId={(row) => row.label}
              data={[
                {
                  label: "기온",
                  keys: DETAIL_METRICS.temperature,
                  suffix: "℃",
                },
                {
                  label: "강수확률",
                  keys: DETAIL_METRICS.probability,
                  suffix: "%",
                },
                {
                  label: "강수량",
                  keys: DETAIL_METRICS.precipitation,
                  suffix: "mm",
                },
                { label: "습도", keys: DETAIL_METRICS.humidity, suffix: "%" },
                { label: "풍속", keys: DETAIL_METRICS.wind, suffix: "m/s" },
                { label: "풍향", keys: DETAIL_METRICS.direction, suffix: "°" },
              ]}
              columns={[
                {
                  accessorKey: "label",
                  header: "한국 시간",
                  size: 76,
                  enableSorting: false,
                },
                ...slots.map(
                  (slot) =>
                    ({
                      id: slot.time,
                      size: 64,
                      enableSorting: false,
                      header: () => (
                        <span
                          title={
                            slot.values[0]?.provider +
                            " · " +
                            (slot.values[0]?.issued_at ?? "발표 시각 미제공")
                          }
                        >
                          <time dateTime={slot.time}>
                            <small>{dateLabel(slot.time)}</small>
                            {hourLabel(slot.time)}
                          </time>
                          <span
                            className="weather-hour-icon"
                            title={condition(slot.values).label}
                          >
                            {condition(slot.values).glyph}
                          </span>
                          <span className="sr-only">
                            {condition(slot.values).label}
                          </span>
                        </span>
                      ),
                      cell: ({ row }) =>
                        row.original.keys === DETAIL_METRICS.direction ? (
                          <WindDirection values={slot.values} />
                        ) : (
                          weatherDetailText(
                            slot.values,
                            row.original.keys,
                            row.original.suffix,
                          )
                        ),
                    }) satisfies ColumnDef<
                      {
                        label: string;
                        keys: readonly string[];
                        suffix: string;
                      },
                      unknown
                    >,
                ),
              ]}
            />
          </div>
        </div>
        <p className="weather-forecast-basis">
          한국 시간 · 예보 시각별 제공처·발표 기준은 열 제목에서 확인할 수
          있습니다.
        </p>
      </section>
    );
  }
  const state = condition(values);
  const tomorrow = tomorrowWeather(outlook, referenceAt);
  const period = (label: string, slot: Slot | undefined) => (
    <p className="weather-tomorrow-period">
      <span>{label}</span>
      {slot ? (
        <>
          <span aria-label={condition(slot.values).label}>
            {condition(slot.values).glyph} {condition(slot.values).label}
          </span>
          <span>
            강수{" "}
            {weatherDetailText(slot.values, DETAIL_METRICS.probability, "%")}
          </span>
        </>
      ) : (
        <span>미제공</span>
      )}
    </p>
  );
  return (
    <section className="weather-detail-overview" aria-label="현재 날씨 상세">
      <div className="weather-day-panels">
        <section className="weather-current-panel" aria-label="현재 날씨">
          <span className="weather-date-badge">
            현재 {dateLabel(referenceAt)}
          </span>
          <div className="weather-current-hero">
            <span aria-hidden="true">{state.glyph}</span>
            <strong>
              {weatherDetailText(values, DETAIL_METRICS.temperature, "°")}
            </strong>
          </div>
          <strong>{state.label}</strong>
          <p>
            최저 {weatherDetailText(values, DETAIL_METRICS.min, "°")} · 최고{" "}
            {weatherDetailText(values, DETAIL_METRICS.max, "°")}
          </p>
        </section>
        <section className="weather-tomorrow-panel" aria-label="내일 날씨">
          <span className="weather-date-badge">
            내일 {tomorrow.date.slice(5).replace("-", ".")}
          </span>
          {outlookPending ? (
            <p role="status">예보 조회 중…</p>
          ) : outlookMessage ? (
            <p role="status">{outlookMessage}</p>
          ) : !tomorrow.rows.length ? (
            <p>내일 예보 미제공</p>
          ) : (
            <>
              <div className="weather-tomorrow-range">
                <span>
                  <small>최저</small>
                  <strong>
                    {weatherDetailText(tomorrow.rows, DETAIL_METRICS.min, "°")}
                  </strong>
                </span>
                <span aria-hidden="true">/</span>
                <span>
                  <small>최고</small>
                  <strong>
                    {weatherDetailText(tomorrow.rows, DETAIL_METRICS.max, "°")}
                  </strong>
                </span>
              </div>
              {period("낮", tomorrow.day)}
              {period("밤", tomorrow.night)}
            </>
          )}
        </section>
      </div>
      <section className="weather-detail-metrics" aria-label="세부 날씨 정보">
        <h3 className="sr-only">미세먼지와 세부 날씨 정보</h3>
        <div className="metric-grid">
          {[
            ["미세먼지", DETAIL_METRICS.pm10, "μg/m³"],
            ["초미세먼지", DETAIL_METRICS.pm25, "μg/m³"],
            ["체감", DETAIL_METRICS.feels, "°"],
            ["풍속", DETAIL_METRICS.wind, "m/s"],
            ["풍향", DETAIL_METRICS.direction, "°"],
            ["습도", DETAIL_METRICS.humidity, "%"],
            ["자외선", DETAIL_METRICS.uv, ""],
          ].map(([label, keys, suffix]) => (
            <div className="metric-card" key={label as string}>
              <span>{label}</span>
              <strong>
                {weatherDetailText(
                  values,
                  keys as readonly string[],
                  suffix as string,
                )}
              </strong>
            </div>
          ))}
          <div className="metric-card weather-sun-times">
            <span>
              일출 <strong>{clockValue(values, DETAIL_METRICS.sunrise)}</strong>
            </span>
            <span>
              일몰 <strong>{clockValue(values, DETAIL_METRICS.sunset)}</strong>
            </span>
          </div>
        </div>
      </section>
    </section>
  );
}
