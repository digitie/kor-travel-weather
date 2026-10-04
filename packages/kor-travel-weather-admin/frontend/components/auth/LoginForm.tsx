"use client";

import { LoginForm as CommonLoginForm, type LoginSubmission } from "@kor-travel/ui";
import { useState } from "react";

import { sanitizeLocalPath } from "@/lib/navigation";

export function LoginForm({ nextPath }: { nextPath: string }) {
  const [error, setError] = useState<string | null>(null);

  async function submit({ credentials, nextPath: destination }: LoginSubmission) {
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...credentials, next: destination }),
      });
      const payload = (await response.json().catch(() => ({}))) as { next?: string };
      if (!response.ok) {
        const messages: Record<number, string> = {
          503: "로그인 환경변수가 설정되지 않았습니다.",
          429: "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.",
          403: "허용되지 않은 요청입니다. 로그인 화면을 새로고침하세요.",
        };
        setError(messages[response.status] ?? "아이디 또는 비밀번호가 올바르지 않습니다.");
        return;
      }
      window.location.assign(sanitizeLocalPath(payload.next ?? destination));
    } catch {
      setError("로그인 요청에 실패했습니다. 네트워크를 확인해 주세요.");
    }
  }

  return (
    <section className="login-shell" aria-labelledby="login-title">
      <div className="login-panel">
        <h1 id="login-title" className="sr-only">관리자 로그인</h1>
        <CommonLoginForm
          brand="Weather Scraper Admin UI"
          description="기상 데이터 수집·복구 관리"
          defaultUsername="admin"
          nextPath={nextPath}
          error={error}
          onClearError={() => setError(null)}
          onSubmit={submit}
        />
      </div>
    </section>
  );
}
