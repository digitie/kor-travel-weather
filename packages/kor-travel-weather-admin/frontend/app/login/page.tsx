"use client";

import { CloudSun, LoaderCircle, LockKeyhole } from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const [nextPath, setNextPath] = useState("/");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const next = new URLSearchParams(window.location.search).get("next");
    if (next?.startsWith("/")) setNextPath(next);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const payload = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "로그인에 실패했습니다.");
      router.replace(nextPath);
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
            <div className="eyebrow">kor-travel-weather</div>
            <h1>Weather source, made visible.</h1>
            <p>원천 응답부터 공개용 forecast projection까지, 한국 여행 데이터의 흐름을 한 화면에서 확인합니다.</p>
          </div>
          <small>operator console · secure access</small>
        </section>
        <section className="login-form-panel">
          <div className="eyebrow">operator access</div>
          <h2>운영 콘솔 로그인</h2>
          <p>관리자 권한이 필요한 화면입니다. 세션은 이 브라우저에만 안전하게 저장됩니다.</p>
          <form className="login-form" onSubmit={submit}>
            <div className="login-field">
              <label htmlFor="username">아이디</label>
              <input autoComplete="username" id="username" required value={username} onChange={(event) => setUsername(event.target.value)} />
            </div>
            <div className="login-field">
              <label htmlFor="password">비밀번호</label>
              <input autoComplete="current-password" id="password" required type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
            </div>
            {error ? <div className="login-error" role="alert">{error}</div> : null}
            <button disabled={loading} type="submit">
              {loading ? <LoaderCircle aria-hidden="true" className="spin" size={16} /> : <LockKeyhole aria-hidden="true" size={16} />}
              {loading ? "확인 중…" : "콘솔 들어가기"}
            </button>
          </form>
          <div className="login-note">접속 문제가 있으면 배포 담당자에게 UI 계정과 reverse proxy 상태를 확인해 주세요.</div>
        </section>
      </div>
    </main>
  );
}
