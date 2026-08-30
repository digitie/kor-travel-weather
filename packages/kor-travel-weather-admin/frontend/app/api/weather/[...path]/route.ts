import { NextRequest } from "next/server";

const apiBase = () => process.env.WEATHER_API_INTERNAL_URL ?? "http://127.0.0.1:12101";

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
  const response = await fetch(target, { method: request.method, headers, body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(), cache: "no-store" });
  return new Response(response.body, { status: response.status, headers: { "content-type": response.headers.get("content-type") ?? "application/json", "x-request-id": response.headers.get("x-request-id") ?? "" } });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
