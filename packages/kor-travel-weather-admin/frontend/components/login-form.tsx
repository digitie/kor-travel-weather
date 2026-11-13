"use client";

import { useState } from "react";
import { LoginForm as CommonLoginForm, type LoginSubmission } from "@kor-travel/ui/login-form";
import { sanitizeLocalPath } from "@kor-travel/ui/navigation";

/** 인증·세션은 BFF(app/api/auth)가 소유하고 폼은 공통 컴포넌트를 사용한다 — map LoginForm과 같은 구조. */
export function LoginForm({ nextPath }: { nextPath: string }) {
  const [error, setError] = useState<string | null>(null);

  async function submit({ credentials, nextPath: destination }: LoginSubmission) {
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...credentials, next: destination }),
      });
      if (!response.ok) {
        const failure = (await response.json().catch(() => ({}))) as { detail?: unknown };
        // 503은 원인이 여러 가지다(설정 누락, rate-limit 저장소 응답 지연 등) — 늘 설정 탓으로
        // 표시하지 말고 서버가 준 사유를 보여 준다.
        const serverDetail = typeof failure.detail === "string" ? failure.detail : null;
        const messages: Record<number, string> = {
          503: serverDetail ?? "로그인 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
          429: "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.",
          403: "허용되지 않은 요청입니다. 로그인 화면을 새로고침하세요.",
        };
        setError(messages[response.status] ?? "아이디 또는 비밀번호가 올바르지 않습니다.");
        return;
      }
      const payload = (await response.json().catch(() => ({}))) as { next?: string };
      window.location.assign(sanitizeLocalPath(payload.next ?? destination));
    } catch {
      setError("로그인 요청에 실패했습니다. 네트워크를 확인해 주세요.");
    }
  }

  return (
    <main className="map-common-surface flex min-h-dvh flex-col justify-center bg-surface-page text-text-primary">
      <h1 className="sr-only">관리자 로그인</h1>
      <CommonLoginForm
        brand="Weather Scraper Admin UI"
        description="기상 데이터 수집·복구 관리"
        defaultUsername="admin"
        nextPath={nextPath}
        onSubmit={submit}
        error={error}
        onClearError={() => setError(null)}
        testId="weather-common-login"
        footer={<p>Weather Scraper Admin UI · 내부 전용 콘솔</p>}
      />
    </main>
  );
}
