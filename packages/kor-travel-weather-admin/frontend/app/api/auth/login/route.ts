import { NextRequest, NextResponse } from "next/server";

import { isAllowedOrigin } from "@/lib/origin";
import { sanitizeLocalPath } from "@/lib/navigation";
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
  return candidate || null;
}

function trustedForwardedIp(request: NextRequest) {
  // X-Forwarded-For/X-Real-IP are client-controlled unless the deployment
  // proxy strips and rewrites them. Opt in only when that proxy contract is
  // explicitly configured; otherwise use the request's socket address.
  if (process.env[TRUST_PROXY_ENV]?.trim().toLowerCase() !== "true") return null;
  // Proxies commonly append the client address to an existing chain. The
  // right-most value is the address added by the trusted last hop, while a
  // caller-supplied left-most value may be spoofed.
  const candidate = (request.headers.get("x-forwarded-for") ?? request.headers.get("x-real-ip"))
    ?.split(",")
    .at(-1)
    ?.trim();
  return candidate && candidate.length <= 64 ? candidate : null;
}

function clientKey(request: NextRequest) {
  const address = trustedForwardedIp(request) ?? socketIp(request);
  // NextRequest does not expose a socket address in every adapter/runtime.
  // Never put all such callers in one global bucket: a single attacker must
  // not be able to lock out every operator. The deployment proxy must supply
  // a trusted address (or configure an adapter that exposes request.ip).
  return address ? `ip:${address.slice(0, 64)}` : null;
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

function rateLimit(key: string | null) {
  if (!key) return null;
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

/** Read a request body without buffering bytes beyond the configured limit. */
async function readBoundedText(request: NextRequest, maxBytes: number): Promise<string | null> {
  const reader = request.body?.getReader();
  if (!reader) {
    const text = await request.text();
    return new TextEncoder().encode(text).byteLength <= maxBytes ? text : null;
  }
  const decoder = new TextDecoder();
  let total = 0;
  let text = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) return text + decoder.decode();
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      return null;
    }
    text += decoder.decode(value, { stream: true });
  }
}

export async function POST(request: NextRequest) {
  if (!isAllowedOrigin(request)) {
    return NextResponse.json(
      { detail: "교차 사이트 요청이 차단되었습니다." },
      { status: 403, headers: { "cache-control": "no-store" } },
    );
  }
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
  let body: { username?: unknown; password?: unknown; next?: unknown };
  try {
    const raw = await readBoundedText(request, MAX_BODY_BYTES);
    if (raw === null) {
      return NextResponse.json(
        { detail: "로그인 요청이 너무 큽니다." },
        { status: 413, headers: { "cache-control": "no-store" } },
      );
    }
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("object expected");
    body = parsed as { username?: unknown; password?: unknown; next?: unknown };
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
  const nextPath = sanitizeLocalPath(body.next);
  if (key) attempts.delete(key);
  const response = NextResponse.json(
    { ok: true, username, next: nextPath },
    { headers: { "cache-control": "no-store" } },
  );
  const forwardedProtocol = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() ?? new URL(request.url).protocol.replace(":", "");
  response.cookies.set({
    name: SESSION_COOKIE,
    value: await createSessionValue(username, secret),
    httpOnly: true,
    maxAge: SESSION_MAX_AGE,
    path: "/",
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production" && forwardedProtocol === "https",
  });
  return response;
}
