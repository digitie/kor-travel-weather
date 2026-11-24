import {NextRequest} from "next/server";
import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
const auth=vi.hoisted(()=>({allowed:true,origin:true,fresh:[] as (boolean|undefined)[]}));
vi.mock("@/lib/session",async(importOriginal)=>({adminUsername:()=>"fixture",SESSION_COOKIE:"fixture",sessionSecret:()=>"fixture",verifySessionValue:async()=>auth.allowed?"fixture":null,durableSessionCacheAllowed:(await importOriginal<typeof import("@/lib/session")>()).durableSessionCacheAllowed,checkDurableSession:async(_session:string,_env:unknown,options?:{fresh?:boolean})=>{auth.fresh.push(options?.fresh);return "active";}}));
vi.mock("@/lib/origin",()=>({isAllowedOrigin:()=>auth.origin}));
import {POST} from "./route";
const request=(body:unknown,origin="https://admin.fixture")=>new NextRequest(origin+"/api/dagster/console",{method:"POST",headers:{origin,"content-type":"application/json",cookie:"fixture=value"},body:typeof body==="string"?body:JSON.stringify(body)});
beforeEach(()=>{auth.allowed=true;auth.origin=true;auth.fresh=[];});afterEach(()=>vi.unstubAllGlobals());
describe("관리자 콘솔 BFF 보호",()=>{
 it("콘솔 POST는 캐시된 세션 판정을 쓰지 않는다",async()=>{vi.stubGlobal("fetch",vi.fn());await POST(request({query:"mutation x{}"}));expect(auth.fresh).toEqual([true]);});
 it("로그인 없으면 native에 접근하지 않는다",async()=>{auth.allowed=false;const fetch=vi.fn();vi.stubGlobal("fetch",fetch);expect((await POST(request({operation:"catalog"}))).status).toBe(401);expect(fetch).not.toHaveBeenCalled();});
 it("교차출처를 거절한다",async()=>{auth.origin=false;const fetch=vi.fn();vi.stubGlobal("fetch",fetch);expect((await POST(request({operation:"catalog"}))).status).toBe(403);expect(fetch).not.toHaveBeenCalled();});
 it.each([{query:"mutation x{}"},{operation:"action",action:"launch",name:"fixture",requestId:"11111111-1111-4111-8111-111111111111",runConfigData:{}}])("임의 query/config를 보내지 않는다",async body=>{const fetch=vi.fn();vi.stubGlobal("fetch",fetch);expect((await POST(request(body))).status).toBe(422);expect(fetch).not.toHaveBeenCalled();});
 it("본문 상한을 유지한다",async()=>{const fetch=vi.fn();vi.stubGlobal("fetch",fetch);expect((await POST(request("x".repeat(4097)))).status).toBe(413);expect(fetch).not.toHaveBeenCalled();});
 it("native 원문 에러를 정적 오류로 바꾼다",async()=>{vi.stubGlobal("fetch",vi.fn().mockResolvedValue(Response.json({errors:[{message:"private-canary https://private.invalid"}]})));const res=await POST(request({operation:"catalog"}));expect(res.status).toBe(502);expect(await res.text()).not.toContain("private-canary");expect(res.headers.get("cache-control")).toContain("no-store");});
});
