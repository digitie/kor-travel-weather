import {afterEach,describe,expect,it,vi} from "vitest";
import {getConsoleCatalog,getConsoleRun,performConsoleAction,ConsoleRequestError} from "./dagster-console-client";
afterEach(()=>vi.unstubAllGlobals());
describe("고정 콘솔 클라이언트",()=>{
 it("본문은 고정 명령이며 GraphQL/config를 보내지 않는다",async()=>{const fetch=vi.fn().mockResolvedValue(Response.json({jobs:[]}));vi.stubGlobal("fetch",fetch);await getConsoleCatalog();expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({operation:"catalog"});expect(fetch.mock.calls[0][0]).toBe("/api/dagster/console");});
 it("불명확한 실행 요청을 자동 재전송하지 않는다",async()=>{const fetch=vi.fn().mockRejectedValue(Error("private-canary"));vi.stubGlobal("fetch",fetch);const error=await performConsoleAction({operation:"action",action:"launch",name:"fixture_job",requestId:"11111111-1111-4111-8111-111111111111"}).catch(e=>e);expect(error).toBeInstanceOf(ConsoleRequestError);expect(error.uncertain).toBe(true);expect(error.message).not.toContain("private-canary");expect(fetch).toHaveBeenCalledTimes(1);});
 it.each([401,403,404,422,502])("%s 원문은 공개하지 않는다",async status=>{vi.stubGlobal("fetch",vi.fn().mockResolvedValue(Response.json({detail:"private-canary https://private.invalid"},{status})));await expect(getConsoleRun("11111111-1111-4111-8111-111111111111")).rejects.not.toThrow("private-canary");});
 it("취소된 조회는 네트워크를 시작하지 않는다",async()=>{const fetch=vi.fn();vi.stubGlobal("fetch",fetch);const c=new AbortController();c.abort();await expect(getConsoleCatalog(c.signal)).rejects.toThrow();expect(fetch).not.toHaveBeenCalled();});
});
