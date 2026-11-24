import { NextRequest, NextResponse } from "next/server";
import { isAllowedOrigin } from "@/lib/origin";
import { sanitizeLocalPath } from "@/lib/navigation";
import {
  adminUsername,
  checkDurableSession,
  forgetDurableSession,
  SESSION_COOKIE,
  sessionSecret,
  verifySessionValue,
} from "@/lib/session";

function unauthorized() {
  return new NextResponse("관리자 UI 인증이 필요합니다.", {
    status: 401,
  });
}

function sessionStoreUnavailable() {
  return new NextResponse("인증 저장소 응답을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.", {
    status: 503,
    headers: { "cache-control": "no-store", "retry-after": "5" },
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
    if ((await verifySessionValue(session, secret, request, username)) === username) {
      const state = await checkDurableSession(session);
      if (state === "unavailable") return sessionStoreUnavailable();
      if (state === "active") {
        const nextPath = sanitizeLocalPath(request.nextUrl.searchParams.get("next"));
        return NextResponse.redirect(new URL(nextPath, request.url));
      }
    }
  } catch {
    // The login page remains visible so the API can return a useful 503.
  }
  return null;
}

// 로그아웃과 재로그인은 요청의 기존 쿠키를 폐기한다. middleware는 route와 다른 runtime의
// 메모리를 쓸 수 있으므로, durable 확인 캐시가 있는 이곳에서 폐기 기록 전에 그 쿠키를 잊는다.
// 실제 폐기·쿠키 삭제·Origin 거부 응답은 route가 그대로 처리한다.
async function forgetRotatedSession(request: NextRequest) {
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  if (request.method !== "POST" || !session || !isAllowedOrigin(request)) return;
  try {
    const username = adminUsername();
    const secret = sessionSecret(username, process.env.WEATHER_UI_PASSWORD ?? "");
    if ((await verifySessionValue(session, secret, request, username)) === username) {
      forgetDurableSession(session);
    }
  } catch {
    // 설정 오류면 이 쿠키로 캐시된 판정도 없다. 응답은 route가 같은 판정으로 만든다.
  }
}

export async function middleware(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  // Login/logout are deliberately public; they establish/clear the signed
  // session used by the rest of the admin surface.
  if (pathname === "/login") {
    return (await redirectAuthenticatedLogin(request)) ?? NextResponse.next();
  }
  if (pathname === "/api/auth/login" || pathname === "/api/auth/logout") {
    await forgetRotatedSession(request);
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
    const state = await checkDurableSession(session);
    if (state === "active") return NextResponse.next();
    if (state === "unavailable") return sessionStoreUnavailable();
  }
  // Keep the web surface session-only, matching kor-travel-geo. Basic Auth is
  // reserved for the separately protected Dagster gateway; accepting it here
  // would let browsers silently bypass logout because they resend cached
  // Authorization headers after the session cookie has been revoked.
  if (request.headers.get("accept")?.includes("text/html")) {
    const login = new URL("/login", request.url);
    login.searchParams.set("next", `${pathname}${request.nextUrl.search}`);
    return NextResponse.redirect(login);
  }
  return unauthorized();
}

// `public/maplibre/`는 node_modules의 maplibre-gl worker 복사본(scripts/copy-maplibre-worker.mjs)이다.
// 공개 라이브러리 코드라 인증 대상이 아니고, 인증 저장소가 느릴 때 worker가 503을 받으면 지도가
// 타일을 하나도 요청하지 못한다. 접두사는 `maplibre/`까지 정확히 맞춰 다른 경로는 계속 보호한다.
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|maplibre/).*)"],
};
