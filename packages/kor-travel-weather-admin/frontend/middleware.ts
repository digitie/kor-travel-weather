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

function configuredPublicOrigins() {
  return (process.env.WEATHER_UI_PUBLIC_ORIGINS ?? process.env.WEATHER_UI_PUBLIC_ORIGIN ?? "")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean)
    .flatMap((origin) => {
      try {
        const parsed = new URL(origin);
        return parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash
          ? []
          : [parsed.origin];
      } catch {
        return [];
      }
    });
}

function normalizeOrigin(origin: string) {
  try {
    const parsed = new URL(origin);
    return parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash
      ? null
      : parsed.origin;
  } catch {
    return null;
  }
}

function sameOriginForMutation(request: NextRequest) {
  const origin = normalizeOrigin(request.headers.get("origin") ?? "");
  if (!origin) return false;
  const allowlist = configuredPublicOrigins();
  if (allowlist.length) return allowlist.includes(origin);
  // A production deployment without an explicit public origin must fail
  // closed. Development keeps the local-origin convenience because its
  // authentication middleware is disabled.
  return process.env.NODE_ENV !== "production" && origin === request.nextUrl.origin;
}

async function durableSessionRevoked(session: string): Promise<boolean> {
  if (process.env.NODE_ENV !== "production") return false;
  const apiBase = process.env.WEATHER_API_INTERNAL_URL?.trim();
  const adminToken = process.env.WEATHER_ADMIN_TOKEN?.trim();
  // A production web container must have the internal API/token pair. Treat
  // a missing pair or an unavailable API as revoked rather than accepting a
  // stateless cookie after a restart.
  if (!apiBase || !adminToken) return true;
  try {
    const response = await fetch(`${apiBase.replace(/\/$/, "")}/v1/admin/session-revocations/check`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-admin-token": adminToken },
      body: JSON.stringify({ session }),
      cache: "no-store",
    });
    if (!response.ok) return true;
    const payload = (await response.json()) as { revoked?: unknown };
    return payload.revoked === true;
  } catch {
    return true;
  }
}

async function redirectAuthenticatedLogin(request: NextRequest) {
  const username = process.env.WEATHER_UI_USER;
  const password = process.env.WEATHER_UI_PASSWORD;
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  if (!username || !password || !session) return null;
  try {
    const secret = sessionSecret(username, password);
    if ((await verifySessionValue(session, secret)) === username && !(await durableSessionRevoked(session))) {
      return NextResponse.redirect(new URL("/", request.url));
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
  const username = process.env.WEATHER_UI_USER;
  const password = process.env.WEATHER_UI_PASSWORD;
  if (!username || !password) {
    return new NextResponse("WEATHER_UI_USER/WEATHER_UI_PASSWORD 설정이 필요합니다.", { status: 503 });
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
    if (!sameOriginForMutation(request)) return csrfBlocked();
  }
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  if (session && (await verifySessionValue(session, secret)) === username) {
    if (!(await durableSessionRevoked(session))) return NextResponse.next();
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
