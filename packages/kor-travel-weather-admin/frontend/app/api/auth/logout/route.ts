import { NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE } from "@/lib/session";

export async function POST(request: NextRequest) {
  const response = NextResponse.json({ ok: true });
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
