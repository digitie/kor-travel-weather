import { NextRequest, NextResponse } from "next/server";
import { isAllowedOrigin } from "@/lib/origin";
import { adminUsername, checkDurableSession, SESSION_COOKIE, sessionSecret, verifySessionValue } from "@/lib/session";
import { ConsoleServiceError, handleConsoleRequest, parseConsoleRequest } from "@/lib/dagster-console-server";

const headers = { "cache-control": "no-store, private" };
async function authenticated(request: NextRequest) {
  const username = adminUsername(), value = request.cookies.get(SESSION_COOKIE)?.value;
  if (!username || !value) return false;
  try {
    const secret = sessionSecret(username, process.env.WEATHER_UI_PASSWORD ?? "");
    return await verifySessionValue(value, secret, request, username) === username && await checkDurableSession(value) === "active";
  } catch { return false; }
}

async function bounded(stream: ReadableStream<Uint8Array> | null, limit: number) {
  if (!stream) return "";
  const reader = stream.getReader(), decoder = new TextDecoder(); let size = 0, text = "";
  try {
    while (true) {
      const chunk = await reader.read(); if (chunk.done) return text + decoder.decode();
      size += chunk.value.byteLength; if (size > limit) throw new ConsoleServiceError(413);
      text += decoder.decode(chunk.value, { stream: true });
    }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

export async function POST(request: NextRequest) {
  if (!(await authenticated(request))) return NextResponse.json({ detail: "로그인이 필요합니다." }, { status: 401, headers });
  if (!isAllowedOrigin(request)) return NextResponse.json({ detail: "허용되지 않은 요청입니다." }, { status: 403, headers });
  if (!request.headers.get("content-type")?.startsWith("application/json")) return NextResponse.json({ detail: "JSON 요청만 허용합니다." }, { status: 415, headers });
  let input;
  try { input = parseConsoleRequest(JSON.parse(await bounded(request.body,4096))); }
  catch (error) { return NextResponse.json({ detail: "요청 본문을 확인해 주세요." }, { status: error instanceof ConsoleServiceError ? error.status : 422, headers }); }
  if (!input) return NextResponse.json({ detail: "허용되지 않은 콘솔 요청입니다." }, { status: 422, headers });
  const deadline = Date.now() + 30_000;
  const graphql = async (query: string, variables: Record<string,unknown>) => {
    const remaining = deadline - Date.now(); if (remaining <= 0) throw new ConsoleServiceError(504);
    const base = (process.env.DAGSTER_UI_INTERNAL_URL ?? "http://127.0.0.1:14102").replace(/\/$/, "");
    const response = await fetch(`${base}/graphql`, { method: "POST", headers: { "content-type": "application/json", accept: "application/json" }, body: JSON.stringify({ query,variables }), cache: "no-store", signal: AbortSignal.timeout(Math.min(10_000,remaining)) });
    if (!response.ok) throw new ConsoleServiceError(502);
    const value = JSON.parse(await bounded(response.body,4_194_304));
    if (value.errors?.length || !value.data) throw new ConsoleServiceError(502);
    return value.data;
  };
  try { return NextResponse.json(await handleConsoleRequest(input,graphql), { headers }); }
  catch (error) { return NextResponse.json({ detail: "Dagster 상태를 확인하지 못했습니다." }, { status: error instanceof ConsoleServiceError ? error.status : 502,headers }); }
}
