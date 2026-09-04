import type { Metadata } from "next";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";

import { LoginForm } from "@/components/auth/LoginForm";
import { sanitizeLocalPath } from "@/lib/navigation";
import {
  adminUsername,
  SESSION_COOKIE,
  durableSessionRevoked,
  sessionSecret,
  verifySessionValue,
} from "@/lib/session";

export const metadata: Metadata = {
  title: "로그인",
};

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

/**
 * Keep the login route server-authoritative, like kor-travel-geo.  A valid session is redirected
 * before the form is streamed, while the client form only handles credential submission and UX.
 */
export default async function LoginPage({ searchParams }: { searchParams?: SearchParams }) {
  const params = (await searchParams) ?? {};
  const nextPath = sanitizeLocalPath(
    typeof params.next === "string" ? params.next : undefined,
  );
  const [cookieStore, headerStore] = await Promise.all([cookies(), headers()]);
  const session = cookieStore.get(SESSION_COOKIE)?.value;
  const username = adminUsername();
  const password = process.env.WEATHER_UI_PASSWORD ?? "";

  if (session) {
    let validSession = false;
    try {
      const secret = sessionSecret(username, password);
      validSession =
        (await verifySessionValue(session, secret, headerStore, username)) === username &&
        !(await durableSessionRevoked(session));
    } catch {
      // Keep the form available so the login endpoint can report a useful configuration error.
    }
    // Next's redirect() throws an internal control-flow signal. Keep it outside
    // the verification catch so a valid session cannot be swallowed as an error.
    if (validSession) redirect(nextPath);
  }

  return <LoginForm nextPath={nextPath} />;
}
