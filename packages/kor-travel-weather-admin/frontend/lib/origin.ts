import type { NextRequest } from "next/server";

function normalizeOrigin(value: string) {
  try {
    const parsed = new URL(value);
    if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) {
      return null;
    }
    return parsed.origin;
  } catch {
    return null;
  }
}

/** Match the Docker Manager's exact configured frontend-origin contract. */
export function isAllowedOrigin(request: NextRequest) {
  const origin = normalizeOrigin(request.headers.get("origin") ?? "");
  // Login/logout are state-changing operations.  Require a browser Origin in
  // every environment, just like Docker Manager; an absent header is never
  // treated as same-origin by the API route.
  if (!origin) return false;
  const configured = (process.env.WEATHER_UI_PUBLIC_ORIGINS ?? process.env.WEATHER_UI_PUBLIC_ORIGIN ?? "")
    .split(",")
    .map((value) => normalizeOrigin(value.trim()))
    .filter((value): value is string => Boolean(value));
  if (configured.length) return configured.includes(origin);
  return process.env.NODE_ENV !== "production" && origin === request.nextUrl.origin;
}
