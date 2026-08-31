import { NextRequest, NextResponse } from "next/server";

import { revokeSessionValue, SESSION_COOKIE } from "@/lib/session";

function isAllowedOrigin(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (!origin) return process.env.NODE_ENV !== "production";
  try {
    const parsed = new URL(origin);
    if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) return false;
    const configured = (process.env.WEATHER_UI_PUBLIC_ORIGINS ?? process.env.WEATHER_UI_PUBLIC_ORIGIN ?? "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    if (configured.length) return configured.some((value) => {
      try { return new URL(value).origin === parsed.origin; } catch { return false; }
    });
    return process.env.NODE_ENV !== "production" && parsed.origin === request.nextUrl.origin;
  } catch {
    return false;
  }
}

async function persistRevocation(session: string) {
  if (process.env.NODE_ENV !== "production") return true;
  const apiBase = process.env.WEATHER_API_INTERNAL_URL?.trim();
  const adminToken = process.env.WEATHER_ADMIN_TOKEN?.trim();
  if (!apiBase || !adminToken) return false;
  try {
    const response = await fetch(`${apiBase.replace(/\/$/, "")}/v1/admin/session-revocations/revoke`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-admin-token": adminToken },
      body: JSON.stringify({ session }),
      cache: "no-store",
    });
    return response.ok;
  } catch {
    return false;
  }
}

export async function POST(request: NextRequest) {
  if (!isAllowedOrigin(request)) {
    return NextResponse.json({ detail: "교차 사이트 요청이 차단되었습니다." }, { status: 403 });
  }
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  if (session && !(await persistRevocation(session))) {
    return NextResponse.json(
      { detail: "로그아웃을 완료할 수 없습니다. 잠시 후 다시 시도해 주세요." },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
  revokeSessionValue(session);
  const response = NextResponse.json({ ok: true }, { headers: { "cache-control": "no-store" } });
  const forwardedProtocol = request.headers.get("x-forwarded-proto") ?? new URL(request.url).protocol.replace(":", "");
  response.cookies.set({
    name: SESSION_COOKIE,
    value: "",
    expires: new Date(0),
    httpOnly: true,
    maxAge: 0,
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production" && forwardedProtocol === "https",
  });
  return response;
}
