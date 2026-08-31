import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, sessionSecret, verifySessionValue } from "@/lib/session";

function unauthorized() {
  return new NextResponse("관리자 UI 인증이 필요합니다.", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="kor-travel-weather admin"' },
  });
}

function csrfBlocked() {
  return new NextResponse("교차 사이트 요청이 차단되었습니다.", { status: 403 });
}

export async function middleware(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  // Login/logout are deliberately public; they establish/clear the signed
  // session used by the rest of the admin surface.
  if (pathname === "/login" || pathname === "/api/auth/login" || pathname === "/api/auth/logout") {
    return NextResponse.next();
  }
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
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  if (session && username && password && (await verifySessionValue(session, sessionSecret(username, password)))) {
    return NextResponse.next();
  }
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Basic ")) {
    if (request.headers.get("accept")?.includes("text/html")) {
      const login = new URL("/login", request.url);
      login.searchParams.set("next", `${pathname}${request.nextUrl.search}`);
      return NextResponse.redirect(login);
    }
    return unauthorized();
  }
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
