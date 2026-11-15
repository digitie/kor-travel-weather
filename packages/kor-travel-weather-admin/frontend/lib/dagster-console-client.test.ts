import {afterEach,describe,expect,it,vi} from "vitest";
import {getConsoleCatalog,getConsoleRun,getConsoleRequestStatus,performConsoleAction,ConsoleRequestError} from "./dagster-console-client";
afterEach(()=>vi.unstubAllGlobals());
describe("고정 콘솔 클라이언트",()=>{
 it("본문은 고정 명령이며 GraphQL/config를 보내지 않는다",async()=>{const fetch=vi.fn().mockResolvedValue(Response.json({jobs:[]}));vi.stubGlobal("fetch",fetch);await getConsoleCatalog();expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({operation:"catalog"});expect(fetch.mock.calls[0][0]).toBe("/api/dagster/console");});
 it("불명확한 실행 요청을 자동 재전송하지 않는다",async()=>{const fetch=vi.fn().mockRejectedValue(Error("private-canary"));vi.stubGlobal("fetch",fetch);const error=await performConsoleAction({operation:"action",action:"launch",name:"fixture_job",requestId:"11111111-1111-4111-8111-111111111111"}).catch(e=>e);expect(error).toBeInstanceOf(ConsoleRequestError);expect(error.uncertain).toBe(true);expect(error.message).not.toContain("private-canary");expect(fetch).toHaveBeenCalledTimes(1);});
 it.each([401,403,404,422,502])("%s 원문은 공개하지 않는다",async status=>{vi.stubGlobal("fetch",vi.fn().mockResolvedValue(Response.json({detail:"private-canary https://private.invalid"},{status})));await expect(getConsoleRun("11111111-1111-4111-8111-111111111111")).rejects.not.toThrow("private-canary");});
 it("취소된 조회는 네트워크를 시작하지 않는다",async()=>{const fetch=vi.fn();vi.stubGlobal("fetch",fetch);const c=new AbortController();c.abort();await expect(getConsoleCatalog(c.signal)).rejects.toThrow();expect(fetch).not.toHaveBeenCalled();});
});


describe("전송 뒤 결과 불명 경계",()=>{
 const command={operation:"action",action:"launch",name:"fixture_job",requestId:"11111111-1111-4111-8111-111111111111"} as const;
 const invalidBodies=["{", "null", "{}", '{"detail":"private-canary"}', '{"state":"unverified","message":"private-canary"}', '"accepted"', '[]'];
 it.each(invalidBodies)("HTTP200의 불완전한 실행 결과 %s는 UNKNOWN으로 유지한다",async body=>{
  const fetch=vi.fn().mockResolvedValue(new Response(body,{status:200,headers:{"content-type":"application/json"}}));vi.stubGlobal("fetch",fetch);
  const error=await performConsoleAction(command).catch(e=>e);expect(error).toBeInstanceOf(ConsoleRequestError);expect(error.uncertain).toBe(true);expect(error.message).not.toContain("private-canary");expect(fetch).toHaveBeenCalledTimes(1);
 });
 it.each([408,409,429,500,502,504])("HTTP%s는 처리 완료 여부를 단정하지 않는다",async status=>{
  const fetch=vi.fn().mockResolvedValue(Response.json({detail:"private-canary"},{status}));vi.stubGlobal("fetch",fetch);
  const error=await performConsoleAction(command).catch(e=>e);expect(error).toBeInstanceOf(ConsoleRequestError);expect(error.uncertain).toBe(true);expect(fetch).toHaveBeenCalledTimes(1);
 });
 it.each([400,401,403,404,413,415,422])("HTTP%s의 확정 사전 거절은 UNKNOWN으로 바꾸지 않는다",async status=>{
  const fetch=vi.fn().mockResolvedValue(Response.json({detail:"private-canary"},{status}));vi.stubGlobal("fetch",fetch);
  const error=await performConsoleAction(command).catch(e=>e);expect(error).toBeInstanceOf(ConsoleRequestError);expect(error.uncertain).toBe(false);expect(fetch).toHaveBeenCalledTimes(1);
 });
 it.each(["{","null","{}",'{"state":"unverified","message":"private-canary"}'])("요청 상태의 불완전한 본문 %s도 읽기 오류로 남기고 실행하지 않는다",async body=>{
  const fetch=vi.fn().mockResolvedValue(new Response(body,{status:200,headers:{"content-type":"application/json"}}));vi.stubGlobal("fetch",fetch);
  await expect(getConsoleRequestStatus(command.requestId)).rejects.toBeInstanceOf(ConsoleRequestError);expect(fetch).toHaveBeenCalledTimes(1);expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({operation:"requestStatus",requestId:command.requestId});
 });
 it.each(["accepted","unchanged","blocked","rejected","unknown"])("검증된 %s 결과는 그대로 반환한다",async state=>{
  const result={state,message:"고정 결과"};vi.stubGlobal("fetch",vi.fn().mockResolvedValue(Response.json(result)));
  expect(await performConsoleAction(command)).toEqual(result);
 });
});

it.each([403,502])("HTTP%s 에러 본문을 해석하기 전에 상태로 처리한다",async status=>{
 const response=Response.json({detail:"private-canary"},{status}),read=vi.spyOn(response,"json");vi.stubGlobal("fetch",vi.fn().mockResolvedValue(response));
 await expect(performConsoleAction({operation:"action",action:"launch",name:"fixture_job",requestId:"11111111-1111-4111-8111-111111111111"})).rejects.toBeInstanceOf(ConsoleRequestError);expect(read).not.toHaveBeenCalled();
});
