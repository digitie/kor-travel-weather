import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { WeatherValue } from "@/lib/api";
import { WeatherDetails, hourlyWeather } from "./weather-details";

const row=(key:string,value:number,provider="kma")=>({value_id:key+provider,metric_key:key,value_number:value,value_text:null,unit:key==="TMP"?"deg_c":key==="WSD"?"m/s":"%",provider,dataset_key:"short",target_at:"2026-10-30T15:00:00Z",issued_at:"2026-10-30T14:00:00Z"} as WeatherValue);
describe("날씨 상세",()=>{
 it("자정의 한국 날짜와 0% 강수를 보존하고 미제공 체감을 만들지 않는다",()=>{
  const html=renderToStaticMarkup(<WeatherDetails values={[]} forecast={[row("TMP",13),row("POP",0),row("REH",59)]} condition={()=>({glyph:"☀",label:"맑음"})}/>);
  expect(html).toContain("0%");expect(html).toContain("59%");expect(html).toContain("10.31 0시");expect(html).toContain("강수량");expect(html).toContain("미제공");
  const current=renderToStaticMarkup(<WeatherDetails values={[row("TMP",13)]} condition={()=>({glyph:"☀",label:"맑음"})}/>);
  expect(current).toContain("13°");expect(current).toContain("체감");expect(current).toContain("미제공");
 });
 it("동일 시각의 다른 제공처 항목을 하나의 예보로 섞지 않는다",()=>{
  const slots=hourlyWeather([row("TMP",13),row("REH",59),row("POP",90,"other")]);
  expect(slots[0].values.map(v=>v.provider)).toEqual(["kma","kma"]);
 });
});
