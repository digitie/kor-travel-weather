import type { ConsoleActionResult, ConsoleCatalog, ConsoleRequest, ConsoleRunDetail } from "./dagster-console-model";

export class ConsoleRequestError extends Error {
  constructor(message: string, readonly uncertain = false) { super(message); }
}

function responseFailure(status:number,changing:boolean) {
  const messages:Record<number,string>={401:"다시 로그인해 주세요.",403:"이 작업에 대한 권한이 없습니다.",404:"이 프로젝트의 실행을 찾지 못했습니다.",422:"요청 형식을 확인해 주세요."};
  const message=messages[status]??(changing?"요청 결과가 확인되지 않았습니다. 상태를 조회해 확인하세요.":"Dagster 상세를 읽지 못했습니다.");
  const definitePreflightRejection=[400,401,403,404,413,415,422].includes(status);
  return new ConsoleRequestError(message,changing&&!definitePreflightRejection);
}

/** 원문 GraphQL/config는 브라우저에서 보내지 않는다. 불명확한 변경 요청도 자동 재전송하지 않는다. */
async function request<T>(body: ConsoleRequest, signal?: AbortSignal): Promise<T> {
  signal?.throwIfAborted();
  const changing = body.operation === "action";
  const boundedSignal = signal ? AbortSignal.any([signal, AbortSignal.timeout(35_000)]) : AbortSignal.timeout(35_000);
  let response: Response;
  try {
    response = await fetch("/api/dagster/console", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), cache: "no-store", signal: boundedSignal,
    });
  } catch {
    signal?.throwIfAborted();
    throw new ConsoleRequestError(changing ? "요청 결과가 확인되지 않았습니다. 상태를 조회해 확인하세요. 변경 요청은 다시 보내지 않았습니다." : "Dagster 상세에 연결하지 못했습니다.", changing);
  }
  // 비정상 상태의 원문은 소비하지 않는다. 본문 손상 여부와 무관하게 상태만 분류한다.
  if (!response.ok) {
    await response.body?.cancel().catch(()=>undefined);
    throw responseFailure(response.status,changing);
  }
  const result = await response.json().catch(() => null);
  signal?.throwIfAborted();
  const needsActionResult = changing || body.operation === "requestStatus";
  const validActionResult = !needsActionResult || result !== null && typeof result === "object" && !Array.isArray(result)
    && ["accepted", "unchanged", "blocked", "rejected", "unknown"].includes(result.state) && typeof result.message === "string";
  if (result === null || !validActionResult) throw responseFailure(response.status,changing);
  return result as T;
}

export const getConsoleCatalog = (signal?: AbortSignal) => request<ConsoleCatalog>({ operation: "catalog" }, signal);
export const getConsoleRun = (runId: string, cursor?: string | null, signal?: AbortSignal, includeEvents = true) => request<ConsoleRunDetail>({ operation: "detail", runId, cursor, ...(includeEvents ? {} : { includeEvents: false }) }, signal);
export const performConsoleAction = (body: Extract<ConsoleRequest, { operation: "action" }>) => request<ConsoleActionResult>(body);
export const getConsoleRequestStatus = (requestId: string, signal?: AbortSignal) => request<ConsoleActionResult>({ operation: "requestStatus", requestId }, signal);
