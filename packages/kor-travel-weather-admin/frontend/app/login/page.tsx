"use client";

import { CloudSun, LoaderCircle, LockKeyhole } from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { sanitizeLocalPath } from "@/lib/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [nextPath, setNextPath] = useState("/");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const next = new URLSearchParams(window.location.search).get("next");
    setNextPath(sanitizeLocalPath(next));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username, password, next: nextPath }),
      });
      const payload = (await response.json().catch(() => ({}))) as { detail?: string; next?: unknown };
      if (!response.ok) throw new Error(payload.detail ?? "로그인에 실패했습니다.");
      router.replace(sanitizeLocalPath(payload.next ?? nextPath));
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-page">
      <div className="login-shell panel">
        <section className="login-intro">
          <div className="login-mark" aria-hidden="true"><CloudSun size={27} /></div>
          <div>
            <div className="eyebrow">Weather Scraper Admin UI</div>
            <h1>Weather Scraper 로그인</h1>
          </div>
        </section>
        <section className="login-form-panel">
          <form aria-busy={loading} className="login-form" onSubmit={submit}>
            <div className="login-field">
              <label htmlFor="username">아이디</label>
              <input aria-describedby={error ? "login-error" : undefined} aria-invalid={Boolean(error)} autoComplete="username" id="username" name="username" required value={username} onChange={(event) => setUsername(event.target.value)} />
            </div>
            <div className="login-field">
              <label htmlFor="password">비밀번호</label>
              <input aria-describedby={error ? "login-error" : undefined} aria-invalid={Boolean(error)} autoComplete="current-password" id="password" name="password" required type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
            </div>
            {error ? <div className="login-error" id="login-error" role="alert">{error}</div> : null}
            <button disabled={loading} type="submit">
              {loading ? <LoaderCircle aria-hidden="true" className="spin" size={16} /> : <LockKeyhole aria-hidden="true" size={16} />}
              {loading ? "확인 중…" : "로그인"}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}
