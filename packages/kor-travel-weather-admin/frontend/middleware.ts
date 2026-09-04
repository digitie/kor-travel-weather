import { NextRequest, NextResponse } from "next/server";
import { isAllowedOrigin } from "@/lib/origin";
import { sanitizeLocalPath } from "@/lib/navigation";
import {
  adminUsername,
  durableSessionRevoked,
  SESSION_COOKIE,
  sessionSecret,
  verifySessionValue,
} from "@/lib/session";

function unauthorized() {
  return new NextResponse("관리자 UI 인증이 필요합니다.", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="kor-travel-weather admin"' },
  });
}

function csrfBlocked() {
  return new NextResponse("교차 사이트 요청이 차단되었습니다.", { status: 403 });
}

async function redirectAuthenticatedLogin(request: NextRequest) {
  const username = adminUsername();
  const password = process.env.WEATHER_UI_PASSWORD ?? "";
  const passwordHash = process.env.WEATHER_UI_PASSWORD_HASH?.trim();
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  if ((!password && !passwordHash) || !session) return null;
  try {
    const secret = sessionSecret(username, password);
    if (
      (await verifySessionValue(session, secret, request, username)) === username &&
      !(await durableSessionRevoked(session))
    ) {
      const nextPath = sanitizeLocalPath(request.nextUrl.searchParams.get("next"));
      return NextResponse.redirect(new URL(nextPath, request.url));
    }
  } catch {
    // The login page remains visible so the API can return a useful 503.
  }
  return null;
}

export async function middleware(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  // Login/logout are deliberately public; they establish/clear the signed
  // session used by the rest of the admin surface.
  if (pathname === "/login") {
    return (await redirectAuthenticatedLogin(request)) ?? NextResponse.next();
  }
  if (pathname === "/api/auth/login" || pathname === "/api/auth/logout") {
    return NextResponse.next();
  }
  if (process.env.NODE_ENV !== "production") return NextResponse.next();
  const username = adminUsername();
  const password = process.env.WEATHER_UI_PASSWORD ?? "";
  const passwordHash = process.env.WEATHER_UI_PASSWORD_HASH?.trim();
  if ((!password && !passwordHash) || !username) {
    return new NextResponse(
      "WEATHER_UI_USER와 WEATHER_UI_PASSWORD 또는 WEATHER_UI_PASSWORD_HASH 설정이 필요합니다.",
      { status: 503 },
    );
  }
  let secret: string;
  try {
    // Validate the production session key even when the request uses the
    // Basic-Auth fallback. Otherwise a placeholder key would silently leave
    // the deployment with no usable signed-session boundary.
    secret = sessionSecret(username, password);
  } catch {
    return new NextResponse("WEATHER_UI_SESSION_SECRET 설정이 필요합니다.", { status: 503 });
  }
  if (["POST", "PATCH", "PUT", "DELETE"].includes(request.method)) {
    // Basic credentials are cached by browsers, so an attacker could
    // otherwise submit a cross-site form to the server-side admin proxy.
    // Require an exact same-origin Origin on every state-changing request.
    if (!isAllowedOrigin(request)) return csrfBlocked();
  }
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  if (session && (await verifySessionValue(session, secret, request, username)) === username) {
    if (!(await durableSessionRevoked(session))) return NextResponse.next();
  }
  // A PBKDF2-only deployment has no cleartext value with which to support the
  // reverse-proxy Basic fallback; it must use the signed session established by
  // the login endpoint instead.
  // When both values are present, the hash is authoritative as well: keeping
  // Basic enabled would leave the old cleartext credential valid after a hash
  // rotation and would no longer match the Geo auth contract.
  if (!password || passwordHash) return unauthorized();
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
