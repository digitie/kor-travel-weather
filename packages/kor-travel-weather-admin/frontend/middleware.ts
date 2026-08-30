import { NextRequest, NextResponse } from "next/server";

function unauthorized() {
  return new NextResponse("관리자 UI 인증이 필요합니다.", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="kor-travel-weather admin"' },
  });
}

function csrfBlocked() {
  return new NextResponse("교차 사이트 요청이 차단되었습니다.", { status: 403 });
}

export function middleware(request: NextRequest) {
  if (process.env.NODE_ENV !== "production") return NextResponse.next();
  const username = process.env.WEATHER_UI_USER;
  const password = process.env.WEATHER_UI_PASSWORD;
  if (!username || !password) {
    return new NextResponse("WEATHER_UI_USER/WEATHER_UI_PASSWORD 설정이 필요합니다.", { status: 503 });
  }
  if (["POST", "PATCH", "PUT", "DELETE"].includes(request.method)) {
    // Basic credentials are cached by browsers, so an attacker could
    // otherwise submit a cross-site form to the server-side admin proxy.
    // Require an exact same-origin Origin on every state-changing request.
    if (request.headers.get("origin") !== request.nextUrl.origin) return csrfBlocked();
  }
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Basic ")) return unauthorized();
  const encoded = authorization.slice("Basic ".length);
  let supplied: string;
  try {
    supplied = atob(encoded);
  } catch {
    return unauthorized();
  }
  if (supplied !== `${username}:${password}`) return unauthorized();
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
