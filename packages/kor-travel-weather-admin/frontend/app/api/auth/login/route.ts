import { NextRequest, NextResponse } from "next/server";

import { createSessionValue, SESSION_COOKIE, SESSION_MAX_AGE, sessionSecret } from "@/lib/session";

export async function POST(request: NextRequest) {
  const username = process.env.WEATHER_UI_USER;
  const password = process.env.WEATHER_UI_PASSWORD;
  if (!username || !password) {
    return NextResponse.json({ detail: "관리자 UI 인증 설정이 없습니다." }, { status: 503 });
  }
  let body: { username?: unknown; password?: unknown };
  try {
    body = (await request.json()) as { username?: unknown; password?: unknown };
  } catch {
    return NextResponse.json({ detail: "로그인 요청 형식이 올바르지 않습니다." }, { status: 400 });
  }
  if (body.username !== username || body.password !== password) {
    return NextResponse.json({ detail: "아이디 또는 비밀번호가 올바르지 않습니다." }, { status: 401 });
  }
  const response = NextResponse.json({ ok: true });
  response.cookies.set({
    name: SESSION_COOKIE,
    value: await createSessionValue(username, sessionSecret(username, password)),
    httpOnly: true,
    maxAge: SESSION_MAX_AGE,
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
  });
  return response;
}
