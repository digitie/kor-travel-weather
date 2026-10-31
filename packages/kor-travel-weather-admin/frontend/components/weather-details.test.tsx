import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { WeatherValue } from "@/lib/api";
import { WeatherDetails, hourlyWeather, tomorrowWeather, TemperatureGraph } from "./weather-details";

const row=(key:string,value:number,provider="kma")=>({value_id:key+provider,metric_key:key,value_number:value,value_text:null,unit:key==="TMP"?"deg_c":key==="WSD"?"m/s":"%",provider,dataset_key:"short",target_at:"2026-10-30T15:00:00Z",issued_at:"2026-10-30T14:00:00Z"} as WeatherValue);
describe("날씨 상세",()=>{
 it("자정의 한국 날짜와 0% 강수를 보존하고 미제공 체감을 만들지 않는다",()=>{
  const html=renderToStaticMarkup(<WeatherDetails values={[]} forecast={[row("TMP",13),row("POP",0),row("REH",59)]} condition={()=>({glyph:"☀",label:"맑음"})}/>);
  expect(html).toContain("0%");expect(html).toContain("59%");expect(html).toContain("10.31");expect(html).toContain("0시");expect(html).toContain("강수량");expect(html).toContain("미제공");
  const current=renderToStaticMarkup(<WeatherDetails values={[row("TMP",13)]} condition={()=>({glyph:"☀",label:"맑음"})}/>);
  expect(current).toContain("13°");expect(current).toContain("체감");expect(current).toContain("미제공");
 });
 it("동일 시각의 다른 제공처 항목을 하나의 예보로 섞지 않는다",()=>{
  const slots=hourlyWeather([row("TMP",13),row("REH",59),row("POP",90,"other")]);
  expect(slots[0].values.map(v=>v.provider)).toEqual(["kma","kma"]);
 });
});

describe("현재·내일 요약과 마지막 시간별 그래프", () => {
 const at = (key: string, value: number, time: string) => ({...row(key, value), target_at: time, unit: ["TMP", "TMN", "TMX"].includes(key) ? "deg_c" : "%"});
 const condition = () => ({glyph:"☀", label:"맑음"});
 it("한국 시간의 내일만 사용하고 낮·밤 예보와 명시 최저·최고를 표시한다", () => {
  const forecast = [at("TMP",99,"2026-10-31T14:00:00Z"),at("SKY",1,"2026-11-01T03:00:00Z"),at("POP",0,"2026-11-01T03:00:00Z"),at("SKY",1,"2026-11-01T12:00:00Z"),at("TMN",6,"2026-11-01T06:00:00Z"),at("TMX",13,"2026-11-01T06:00:00Z")];
  const result = tomorrowWeather(forecast,"2026-10-31T00:00:00Z");
  expect(result.date).toBe("2026-11-01");expect(result.rows).toHaveLength(5);
  expect(result.day?.time).toBe("2026-11-01T03:00:00.000Z");expect(result.night?.time).toBe("2026-11-01T12:00:00.000Z");
  const html = renderToStaticMarkup(<WeatherDetails values={[row("TMP",11)]} outlook={forecast} referenceAt="2026-10-31T00:00:00Z" condition={condition}/>);
  expect(html).toContain("내일 11.01");expect(html).toContain("6°");expect(html).toContain("13°");expect(html).toContain("강수 0%");
  expect(html.indexOf('aria-label="현재 날씨"')).toBeLessThan(html.indexOf('aria-label="내일 날씨"'));
  expect(html.indexOf('aria-label="내일 날씨"')).toBeLessThan(html.indexOf('aria-label="세부 날씨 정보"'));
 });
 it("부분 시간별 기온을 하루 최저·최고로 만들지 않고 조회 실패와 자료 부재를 구분한다", () => {
  const props = {values:[],outlook:[at("TMP",7,"2026-11-01T03:00:00Z")],referenceAt:"2026-10-31T00:00:00Z",condition};
  const html = renderToStaticMarkup(<WeatherDetails {...props}/>);
  expect(html).not.toContain("7°");expect(html).toContain("미제공");
  expect(renderToStaticMarkup(<WeatherDetails {...props} outlookPending/>)).toContain("예보 조회 중");
  expect(renderToStaticMarkup(<WeatherDetails {...props} outlookMessage="예보 조회 실패"/>)).toContain("예보 조회 실패");
 });
 it("그래프는 영하·0도를 보존하고 결측 또는 두 시간 초과 공백을 선으로 잇지 않는다", () => {
  const slots = [
   {time:"2026-10-31T00:00:00Z",values:[at("TMP",-1,"2026-10-31T00:00:00Z")]},
   {time:"2026-10-31T01:00:00Z",values:[at("TMP",0,"2026-10-31T01:00:00Z")]},
   {time:"2026-10-31T02:00:00Z",values:[]},
   {time:"2026-10-31T03:00:00Z",values:[at("TMP",1,"2026-10-31T03:00:00Z")]},
   {time:"2026-10-31T07:00:00Z",values:[at("TMP",2,"2026-10-31T07:00:00Z")]},
  ];
  const html=renderToStaticMarkup(<TemperatureGraph slots={slots}/>);
  expect(html).toContain("-1°");expect(html).toContain("0°");
  const path=html.match(/<path d="([^"]+)"/)?.[1] ?? "";
  expect(path.match(/M/g)).toHaveLength(3);expect(path.match(/L/g)).toHaveLength(1);
  expect(renderToStaticMarkup(<TemperatureGraph slots={[{...slots[0],values:[{...slots[0].values[0],unit:"K"}]}]}/>)).toContain("그래프로 표시할 기온 자료가 없습니다.");
 });
});

describe("운영 정본 키와 예보 갱신 우선순위",()=>{
 it("서로 다른 데이터셋의 더 최신 발표를 항목 수보다 먼저 선택한다",()=>{
  const old=[row("TMP",10),row("SKY",1),row("POP",10)].map(v=>({...v,dataset_key:"short",issued_at:"2026-10-30T12:00:00Z"}));
  const fresh=[row("TMP",20),row("SKY",3)].map(v=>({...v,dataset_key:"ultra_short",issued_at:"2026-10-30T14:00:00Z"}));
  expect(hourlyWeather([...old,...fresh])[0].values.find(v=>v.metric_key==="TMP")?.value_number).toBe(20);
 });
 it("현재 관측을 이미 시작된 예보 기온으로 덮지 않는다",()=>{
  const values=[{...row("T1H",13),unit:"deg_c",forecast_style:"observed",target_at:"2026-10-31T01:50:00Z"},{...row("TMP",20),forecast_style:"short",target_at:"2026-10-31T02:00:00Z"}];
  const html=renderToStaticMarkup(<WeatherDetails values={values} referenceAt="2026-10-31T02:10:00Z" condition={()=>({glyph:"☀",label:"맑음"})}/>);
  expect(html).toContain("13°");expect(html).not.toContain("20°");
 });
 it("외부 제공처의 정본 강수확률·자외선 키를 표시한다",()=>{
  const values=[{...row("UV_INDEX",3),unit:"index"}];
  const current=renderToStaticMarkup(<WeatherDetails values={values} condition={()=>({glyph:"☀",label:"맑음"})}/>);
  expect(current).toContain("<strong>3</strong>");
  const forecast=renderToStaticMarkup(<WeatherDetails values={[]} forecast={[row("PRECIP_PROB",80)]} condition={()=>({glyph:"☀",label:"맑음"})}/>);
  expect(forecast).toContain("80%");
 });
});
