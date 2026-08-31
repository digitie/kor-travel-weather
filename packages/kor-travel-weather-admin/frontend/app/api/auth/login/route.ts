import { NextRequest, NextResponse } from "next/server";

import { createSessionValue, SESSION_COOKIE, SESSION_MAX_AGE, sessionSecret } from "@/lib/session";

const WINDOW_MS = 10 * 60 * 1000;
const MAX_ATTEMPTS = 5;
const MAX_BODY_BYTES = 4096;
const MAX_TRACKED_KEYS = 4096;
const TRUST_PROXY_ENV = "WEATHER_UI_TRUST_PROXY";
const attempts = new Map<string, { count: number; resetAt: number }>();

type NextRequestWithSocketIp = NextRequest & { ip?: string };

function socketIp(request: NextRequest) {
  const value = (request as NextRequestWithSocketIp).ip;
  const candidate = typeof value === "string" ? value.trim() : "";
  return candidate || "unknown";
}

function trustedForwardedIp(request: NextRequest) {
  // X-Forwarded-For/X-Real-IP are client-controlled unless the deployment
  // proxy strips and rewrites them. Opt in only when that proxy contract is
  // explicitly configured; otherwise use the request's socket address.
  if (process.env[TRUST_PROXY_ENV]?.trim().toLowerCase() !== "true") return null;
  const candidate = (request.headers.get("x-forwarded-for") ?? request.headers.get("x-real-ip"))
    ?.split(",")[0]
    ?.trim();
  return candidate && candidate.length <= 64 ? candidate : null;
}

function clientKey(request: NextRequest) {
  return `ip:${(trustedForwardedIp(request) ?? socketIp(request)).slice(0, 64)}`;
}

function evictOneKey() {
  let oldestKey: string | undefined;
  let oldestResetAt = Number.POSITIVE_INFINITY;
  for (const [key, entry] of attempts) {
    if (entry.resetAt < oldestResetAt) {
      oldestKey = key;
      oldestResetAt = entry.resetAt;
    }
  }
  if (oldestKey !== undefined) attempts.delete(oldestKey);
}

function rateLimit(key: string) {
  const now = Date.now();
  for (const [entryKey, entry] of attempts) {
    if (entry.resetAt <= now) attempts.delete(entryKey);
  }
  const current = attempts.get(key);
  if (!current || current.resetAt <= now) {
    if (attempts.size >= MAX_TRACKED_KEYS) evictOneKey();
    attempts.set(key, { count: 1, resetAt: now + WINDOW_MS });
    return null;
  }
  if (current.count >= MAX_ATTEMPTS) return Math.max(1, Math.ceil((current.resetAt - now) / 1000));
  current.count += 1;
  return null;
}

function tooManyRequests(retryAfter: number) {
  return NextResponse.json({ detail: "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요." }, {
    status: 429,
    headers: { "retry-after": String(retryAfter), "cache-control": "no-store" },
  });
}

export async function POST(request: NextRequest) {
  const username = process.env.WEATHER_UI_USER;
  const password = process.env.WEATHER_UI_PASSWORD;
  if (!username || !password) {
    return NextResponse.json(
      { detail: "관리자 UI 인증 설정이 없습니다." },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
  let secret: string;
  try {
    secret = sessionSecret(username, password);
  } catch {
    return NextResponse.json(
      { detail: "관리자 UI 세션 설정이 올바르지 않습니다." },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
  const key = clientKey(request);
  const retryAfter = rateLimit(key);
  if (retryAfter !== null) return tooManyRequests(retryAfter);
  const declaredLength = Number(request.headers.get("content-length") ?? 0);
  if (declaredLength > MAX_BODY_BYTES) {
    return NextResponse.json(
      { detail: "로그인 요청이 너무 큽니다." },
      { status: 413, headers: { "cache-control": "no-store" } },
    );
  }
  let body: { username?: unknown; password?: unknown };
  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
      return NextResponse.json(
        { detail: "로그인 요청이 너무 큽니다." },
        { status: 413, headers: { "cache-control": "no-store" } },
      );
    }
    body = JSON.parse(raw) as { username?: unknown; password?: unknown };
  } catch {
    return NextResponse.json(
      { detail: "로그인 요청 형식이 올바르지 않습니다." },
      { status: 400, headers: { "cache-control": "no-store" } },
    );
  }
  if (body.username !== username || body.password !== password) {
    return NextResponse.json(
      { detail: "아이디 또는 비밀번호가 올바르지 않습니다." },
      { status: 401, headers: { "cache-control": "no-store" } },
    );
  }
  attempts.delete(key);
  const response = NextResponse.json({ ok: true }, { headers: { "cache-control": "no-store" } });
  const forwardedProtocol = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() ?? new URL(request.url).protocol.replace(":", "");
  response.cookies.set({
    name: SESSION_COOKIE,
    value: await createSessionValue(username, secret),
    httpOnly: true,
    maxAge: SESSION_MAX_AGE,
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production" && forwardedProtocol === "https",
  });
  return response;
}
