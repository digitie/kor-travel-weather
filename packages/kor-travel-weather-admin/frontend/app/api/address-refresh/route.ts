import { NextRequest, NextResponse } from "next/server";
import { isAllowedOrigin } from "@/lib/origin";
import { DAGSTER_LOCATION_NAME, DAGSTER_REPOSITORY_NAME,
  ADDRESS_REFRESH_PREVIOUS_QUERY, ADDRESS_REFRESH_ACTIVE_QUERY, ADDRESS_REFRESH_LAUNCH_MUTATION } from "@/lib/dagster-scope";
import { adminUsername, checkDurableSession, SESSION_COOKIE, sessionSecret, verifySessionValue } from "@/lib/session";

const headers = { "cache-control": "no-store, private" };
const reply = (detail: string, status: number) => NextResponse.json({ detail }, { status, headers });
const JOB = "address_refresh_job";
// 현재 단일 BFF 프로세스에서 조회→예약 사이 동시 진입을 차단한다.
// 여러 replica의 전역 exactly-once를 보장하는 잠금은 아니다.
let submissionInFlight = false;
async function bounded(stream: ReadableStream<Uint8Array> | null, limit: number) {
  if (!stream) return "";
  const reader = stream.getReader(); const decoder = new TextDecoder();
  let size = 0; let text = "";
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) return text + decoder.decode();
      size += chunk.value.byteLength;
      if (size > limit) throw new Error("too_large");
      text += decoder.decode(chunk.value, { stream: true });
    }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
async function authenticated(request: NextRequest) {
  const username = adminUsername();
  const value = request.cookies.get(SESSION_COOKIE)?.value;
  if (!username || !value) return false;
  try {
    const secret = sessionSecret(username, process.env.WEATHER_UI_PASSWORD ?? "");
    return await verifySessionValue(value, secret, request, username) === username
      && await checkDurableSession(value) === "active";
  } catch { return false; }
}
async function graphql(query: string, variables: Record<string, unknown>) {
  const base = (process.env.DAGSTER_UI_INTERNAL_URL ?? "http://127.0.0.1:14102");
  const response = await fetch(base.replace(/\/$/, "") + "/graphql", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ query, variables }), cache: "no-store", signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error("upstream");
  const result = JSON.parse(await bounded(response.body, 131072));
  if (result.errors || !result.data) throw new Error("graphql");
  return result.data;
}
export async function GET(request: NextRequest) {
  if (!(await authenticated(request))) return reply("로그인이 필요합니다.", 401);
  const token = (process.env.WEATHER_ADMIN_TOKEN ?? process.env.KOR_TRAVEL_WEATHER_ADMIN_TOKEN ?? "");
  if (token.length < 16) return reply("관리자 API 토큰이 설정되지 않았습니다.", 503);
  try {
    const response = await fetch(new URL("/v1/admin/address-refresh", (process.env.WEATHER_API_INTERNAL_URL ?? "http://127.0.0.1:14101")), {
      headers: { "x-admin-token": token }, cache: "no-store", signal: AbortSignal.timeout(12_000),
    });
    if (!response.ok) return reply("주소 갱신 상태를 읽지 못했습니다.", response.status);
    const result = JSON.parse(await bounded(response.body, 262144));
    return NextResponse.json(result.data, { headers });
  } catch { return reply("주소 갱신 API에 연결하지 못했습니다.", 502); }
}
export async function POST(request: NextRequest) {
  if (!(await authenticated(request))) return reply("로그인이 필요합니다.", 401);
  if (!isAllowedOrigin(request)) return reply("허용되지 않은 요청입니다.", 403);
  if (!request.headers.get("content-type")?.startsWith("application/json")) return reply("JSON 요청만 허용합니다.", 415);
  let body: Record<string, unknown>;
  try { body = JSON.parse(await bounded(request.body, 1024)); } catch { return reply("요청 본문을 확인해 주세요.", 422); }
  if (!body || Array.isArray(body) || Object.keys(body).length !== 1
    || typeof body.requestId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(body.requestId)) return reply("실행 요청 ID가 올바르지 않습니다.", 422);
  if (submissionInFlight) return reply("주소 갱신 예약 요청을 처리 중입니다. 잠시 후 같은 요청으로 확인하세요.", 409);
  submissionInFlight = true;
  try {
    const previous = await graphql(
      ADDRESS_REFRESH_PREVIOUS_QUERY,
      { location: DAGSTER_LOCATION_NAME, requestId: body.requestId },
    );
    if (previous.runsOrError?.__typename !== "Runs") throw new Error("previous");
    if (previous.runsOrError.results?.[0]) return NextResponse.json({ ...previous.runsOrError.results[0], replayed: true }, { status: 202, headers });
    const active = await graphql(
      ADDRESS_REFRESH_ACTIVE_QUERY,
      { location: DAGSTER_LOCATION_NAME },
    );
    if (active.runsOrError?.__typename !== "Runs") throw new Error("active");
    const existing = active.runsOrError.results?.[0];
    if (existing) return reply("주소 갱신이 이미 예약되었거나 실행 중입니다. 해당 실행 완료 후 다시 시도하세요.", 409);
    const result = await graphql(
      ADDRESS_REFRESH_LAUNCH_MUTATION,
      { executionParams: {
        selector: { repositoryLocationName: DAGSTER_LOCATION_NAME, repositoryName: DAGSTER_REPOSITORY_NAME, pipelineName: JOB },
        runConfigData: { ops: { refresh_addresses: { config: { force: true } } } },
        executionMetadata: { tags: [
          { key: "kortravel/address-request", value: body.requestId },
          { key: "dagster/code_location", value: DAGSTER_LOCATION_NAME },
          { key: "kortravel/address-refresh", value: "manual" },
        ] },
      } },
    );
    if (result.launchRun?.__typename !== "LaunchRunSuccess") return reply("실행을 예약하지 못했습니다. Dagster에서 같은 요청의 실행 여부를 확인하세요.", 502);
    return NextResponse.json({ ...result.launchRun.run, replayed: false }, { status: 202, headers });
  } catch { return reply("예약 결과를 확인하지 못했습니다. 같은 요청으로 다시 시도해 주세요.", 502); }
  finally { submissionInFlight = false; }
}
