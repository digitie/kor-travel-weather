import { describe, expect, it } from "vitest";
import type { WeatherValue } from "@/lib/api";
import { DETAIL_METRICS, weatherDetailText } from "./weather-details";
const row=(value_number:number|null, unit:string|null, value_text:string|null=null)=>({metric_key:"TEMP",value_number,unit,value_text,target_at:"2026-10-30T12:00:00Z",provider:"kma",issued_at:null} as WeatherValue);
describe("실제 날씨 상세의 값·단위 보존",()=>{
 it.each([[0,"0°"],[-2.56,"-2.6°"],[-0.04,"-0.04°"]])("기온 %s의 부호와 작은 비영 값을 보존한다",(value,expected)=>{
  expect(weatherDetailText([row(value as number,"deg_c")],DETAIL_METRICS.temperature,"°")).toBe(expected);
 });
 it("미지원 단위를 섭씨로 바꾸거나 문자열 결측을 숫자0으로 만들지 않는다",()=>{
  expect(weatherDetailText([row(14.4444,"K")],DETAIL_METRICS.temperature,"°")).toBe("14.4444 K");
  expect(weatherDetailText([row(null,"deg_c","<0.1")],DETAIL_METRICS.temperature,"°")).toBe("<0.1°");
  expect(weatherDetailText([row(0,null)],DETAIL_METRICS.temperature,"°")).toBe("0");
  expect(weatherDetailText([],DETAIL_METRICS.temperature,"°")).toBe("미제공");
 });
 it("강수0과 원래 정밀도를 보존한다",()=>{
  for(const value of [0,0.04])expect(weatherDetailText([{...row(value,"mm"),metric_key:"PCP"}],DETAIL_METRICS.precipitation,"mm")).toBe(`${value}mm`);
 });
});
