import { NextRequest, NextResponse } from "next/server";

import { isAllowedOrigin } from "@/lib/origin";
import {
  adminUsername,
  durableRevokeSession,
  revokeSessionValue,
  SESSION_COOKIE,
  sessionSecret,
  verifySessionValue,
} from "@/lib/session";

/**
 * Only persist revocation markers for a session that this deployment issued.
 * An arbitrary cookie must still be cleared, but storing attacker-controlled
 * digests would let the logout endpoint grow the revocation table forever.
 */
async function isConfiguredSession(session: string, request: NextRequest) {
  const username = adminUsername();
  const password = process.env.WEATHER_UI_PASSWORD ?? "";
  try {
    const secret = sessionSecret(username, password);
    return (await verifySessionValue(session, secret, request, username)) === username;
  } catch {
    return false;
  }
}

export async function POST(request: NextRequest) {
  if (!isAllowedOrigin(request)) {
    return NextResponse.json({ detail: "교차 사이트 요청이 차단되었습니다." }, { status: 403 });
  }
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  const validSession = session ? await isConfiguredSession(session, request) : false;
  if (validSession && !(await durableRevokeSession(session!))) {
    return NextResponse.json(
      { detail: "로그아웃을 완료할 수 없습니다. 잠시 후 다시 시도해 주세요." },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
  if (validSession) revokeSessionValue(session);
  const response = NextResponse.json({ ok: true }, { headers: { "cache-control": "no-store" } });
  const forwardedProtocol =
    request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() ??
    new URL(request.url).protocol.replace(":", "");
  response.cookies.set({
    name: SESSION_COOKIE,
    value: "",
    expires: new Date(0),
    httpOnly: true,
    maxAge: 0,
    path: "/",
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production" || forwardedProtocol === "https",
  });
  return response;
}
