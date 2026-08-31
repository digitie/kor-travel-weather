"use client";

import { useMemo, useState } from "react";

import { PageHeader } from "@/components/admin-shell";

type Preset = { label: string; method: "GET" | "POST"; path: string; description: string };

const PRESETS: Preset[] = [
  { label: "Health", method: "GET", path: "/health", description: "API liveness" },
  { label: "공개 위치", method: "GET", path: "/v1/weather/locations?limit=20", description: "enabled catalog" },
  { label: "관리자 위치", method: "GET", path: "/v1/admin/locations?limit=20", description: "관리자 catalog" },
  { label: "Provider catalog", method: "GET", path: "/v1/admin/providers", description: "configured provider" },
  { label: "Sync runs", method: "GET", path: "/v1/admin/sync-runs?limit=20", description: "수집 실행 기록" },
];

export default function ApiTestPage() {
  const [method, setMethod] = useState<"GET" | "POST">("GET");
  const [path, setPath] = useState("/health");
  const [body, setBody] = useState("");
  const [result, setResult] = useState<unknown>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const selectedPreset = useMemo(() => PRESETS.find((preset) => preset.path === path && preset.method === method), [method, path]);

  async function runRequest() {
    setRunning(true);
    setError("");
    setResult(null);
    const started = performance.now();
    try {
      const response = await fetch(`/api/weather${path.startsWith("/") ? path : `/${path}`}`, {
        method,
        headers: { accept: "application/json", ...(method === "POST" ? { "content-type": "application/json" } : {}) },
        body: method === "POST" && body.trim() ? body : undefined,
        cache: "no-store",
      });
      setStatus(response.status);
      setDuration(Math.round(performance.now() - started));
      const text = await response.text();
      try {
        setResult(JSON.parse(text));
      } catch {
        setResult(text);
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "요청을 실행하지 못했습니다.");
      setDuration(Math.round(performance.now() - started));
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <PageHeader
        actions={<span className="status on">proxy ready</span>}
        description="운영 UI 인증 세션을 통해 public/admin API 응답과 문제 envelope를 빠르게 확인합니다."
        section="시스템"
        title="API 테스트"
      />
      <section className="api-console">
        <aside className="api-presets panel">
          <div className="panel-head"><div><h2>빠른 요청</h2><p>자주 확인하는 endpoint</p></div></div>
          <div className="preset-list">{PRESETS.map((preset) => <button key={preset.path} type="button" className={selectedPreset === preset ? "selected" : ""} onClick={() => { setMethod(preset.method); setPath(preset.path); setBody(""); }}><span><strong>{preset.label}</strong><small>{preset.description}</small></span><code>{preset.method}</code></button>)}</div>
        </aside>
        <div className="api-workspace panel">
          <div className="panel-head"><div><h2>요청 작성</h2><p>브라우저에서 직접 backend를 호출하지 않고 same-origin proxy를 사용합니다.</p></div>{status !== null ? <span className={`status ${status < 400 ? "on" : "off"}`}>{status} · {duration}ms</span> : null}</div>
          <div className="api-form">
            <label className="api-field" htmlFor="api-method"><span>Method</span><select id="api-method" aria-label="HTTP method" value={method} onChange={(event) => setMethod(event.target.value as "GET" | "POST")}><option>GET</option><option>POST</option></select></label>
            <label className="api-field" htmlFor="api-path"><span>Path</span><input id="api-path" aria-label="API path" value={path} onChange={(event) => setPath(event.target.value)} placeholder="/v1/weather/locations" /></label>
            <button type="button" className="button primary api-submit" onClick={runRequest} disabled={running}>{running ? "실행 중…" : "요청 실행"}</button>
          </div>
          {method === "POST" ? <label className="api-body-field" htmlFor="api-body"><span>JSON body</span><textarea id="api-body" className="api-body" aria-label="JSON body" value={body} onChange={(event) => setBody(event.target.value)} placeholder={'{"location_id":"seoul"}'} /></label> : null}
          {error ? <div className="error" role="alert">{error}</div> : null}
          <div className="api-result"><div className="section-label"><span>response</span><span>{result === null ? "—" : "JSON"}</span></div><pre>{result === null ? "요청 결과가 여기에 표시됩니다." : typeof result === "string" ? result : JSON.stringify(result, null, 2)}</pre></div>
        </div>
      </section>
    </>
  );
}
