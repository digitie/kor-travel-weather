import { NextRequest } from "next/server";

const apiBase = () => process.env.WEATHER_API_INTERNAL_URL ?? "http://127.0.0.1:14101";
const MAX_PROXY_BODY_BYTES = 1_048_576;

async function readBody(request: NextRequest): Promise<ArrayBuffer | null | undefined> {
  if (request.method === "GET" || request.method === "HEAD") return undefined;
  const declaredLength = Number(request.headers.get("content-length") ?? 0);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_PROXY_BODY_BYTES) return null;
  const reader = request.body?.getReader();
  if (!reader) return new ArrayBuffer(0);
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      total += next.value.byteLength;
      if (total > MAX_PROXY_BODY_BYTES) {
        await reader.cancel();
        return null;
      }
      chunks.push(next.value);
    }
  } finally {
    reader.releaseLock();
  }
  const body = new ArrayBuffer(total);
  const bytes = new Uint8Array(body);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = new URL(`${apiBase()}/${path.join("/")}`);
  request.nextUrl.searchParams.forEach((value, key) => target.searchParams.set(key, value));
  // Forward only harmless content negotiation headers. In particular, do not
  // leak the browser's Basic-Auth header/cookies to the backend; the proxy
  // supplies the server-side admin token below.
  const headers = new Headers();
  for (const name of ["accept", "content-type", "if-none-match", "x-request-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set(
    "x-admin-token",
    process.env.WEATHER_ADMIN_TOKEN ?? process.env.KOR_TRAVEL_WEATHER_ADMIN_TOKEN ?? "",
  );
  const body = await readBody(request);
  if (body === null) {
    return Response.json(
      { detail: "요청 본문이 너무 큽니다." },
      { status: 413, headers: { "cache-control": "no-store" } },
    );
  }
  const response = await fetch(target, { method: request.method, headers, body, cache: "no-store" });
  return new Response(response.body, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
      "cache-control": "no-store, private",
      "x-request-id": response.headers.get("x-request-id") ?? "",
    },
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
