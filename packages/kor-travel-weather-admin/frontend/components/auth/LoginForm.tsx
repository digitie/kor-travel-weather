"use client";

import { LockKeyhole, LogIn } from "lucide-react";
import { FormEvent, useState } from "react";

import { sanitizeLocalPath } from "@/lib/navigation";

export function LoginForm({ nextPath }: { nextPath: string }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username, password, next: nextPath }),
      });
      const payload = (await response.json().catch(() => ({}))) as { detail?: string; next?: string };
      if (response.status === 503) {
        setError(payload.detail ?? "로그인 환경변수가 설정되지 않았습니다.");
        return;
      }
      if (response.status === 429) {
        setError(payload.detail ?? "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.");
        return;
      }
      if (response.status === 403) {
        setError(payload.detail ?? "허용되지 않은 요청입니다. 로그인 화면을 새로고침하세요.");
        return;
      }
      if (!response.ok) {
        setError(payload.detail ?? "아이디 또는 비밀번호가 올바르지 않습니다.");
        return;
      }
      window.location.assign(sanitizeLocalPath(payload.next ?? nextPath));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="login-shell" aria-labelledby="login-title">
      <div className="login-panel">
        <div className="login-brand">
          <div className="login-icon" aria-hidden="true">
            <LockKeyhole size={24} />
          </div>
          <div>
            <p>Weather Scraper Admin UI</p>
            <h1 id="login-title">관리자 로그인</h1>
          </div>
        </div>
        <form aria-busy={busy} className="login-form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="admin-username">아이디</label>
            <input
              aria-describedby="login-error"
              aria-invalid={error ? true : undefined}
              autoComplete="username"
              disabled={busy}
              id="admin-username"
              name="username"
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="admin-password">비밀번호</label>
            <input
              aria-describedby="login-error"
              aria-invalid={error ? true : undefined}
              autoComplete="current-password"
              disabled={busy}
              id="admin-password"
              name="password"
              required
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <button className="button login-submit" disabled={busy} type="submit">
            <LogIn aria-hidden="true" size={17} />
            로그인
          </button>
          <p className="login-error" id="login-error" role="alert" aria-live="assertive">
            {error}
          </p>
        </form>
      </div>
    </section>
  );
}
