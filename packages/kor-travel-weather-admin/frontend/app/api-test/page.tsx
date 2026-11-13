"use client";

import { useMemo, useState } from "react";

import { AdminShell } from "@/components/admin-shell";
import { EmptyState } from "@/components/empty-state";
import { JsonViewer } from "@/components/json-viewer";
import { SectionCard } from "@/components/section-card";
import {
  SelectableRow,
  SelectableRowDescription,
  SelectableRowGroup,
  SelectableRowTitle,
} from "@/components/selectable-row";
import { HttpStatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FormField, FormSelect, FormTextArea } from "@/components/ui/form-field";

type Preset = { label: string; method: "GET" | "POST"; path: string; description: string };

const PRESETS: Preset[] = [
  { label: "상태 확인", method: "GET", path: "/health", description: "서버가 살아있는지 확인" },
  { label: "공개 위치", method: "GET", path: "/v1/weather/locations?limit=20", description: "공개된 위치 목록" },
  { label: "관리자 위치", method: "GET", path: "/v1/admin/locations?limit=20", description: "관리자용 위치 목록" },
  { label: "제공처 목록", method: "GET", path: "/v1/admin/providers", description: "등록된 데이터 제공처 목록" },
  { label: "수집 실행", method: "GET", path: "/v1/admin/sync-runs?limit=20", description: "수집 실행 기록" },
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
      if (!response.ok) {
        setError(`요청 실패 (HTTP ${response.status}). 아래 응답을 확인하세요.`);
        const failure = await response.text();
        try { setResult(JSON.parse(failure)); } catch { setResult(failure); }
        return;
      }
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
    <AdminShell
      actions={<Badge variant="success">연결됨</Badge>}
      description="로그인한 관리자 세션으로 API를 직접 호출해서 응답을 확인하는 도구입니다."
      section="시스템"
      title="API 테스트"
    >
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(16rem,20rem)_minmax(0,1fr)]">
        <SectionCard description="자주 쓰는 API 요청" title="빠른 요청">
          <SelectableRowGroup aria-label="빠른 요청 목록" className="flex flex-col gap-1">
            {PRESETS.map((preset) => (
              <SelectableRow
                key={preset.path}
                selected={selectedPreset === preset}
                trailing={<code className="font-mono text-xs text-text-secondary">{preset.method}</code>}
                onSelect={() => { setMethod(preset.method); setPath(preset.path); setBody(""); }}
              >
                <SelectableRowTitle>{preset.label}</SelectableRowTitle>
                <SelectableRowDescription>{preset.description}</SelectableRowDescription>
              </SelectableRow>
            ))}
          </SelectableRowGroup>
        </SectionCard>

        <SectionCard
          actions={status !== null ? (
            <div className="flex items-center gap-2">
              <HttpStatusBadge code={status} />
              <span className="text-xs text-text-secondary tabular-nums">{duration}ms</span>
            </div>
          ) : null}
          description="관리자 서버를 거쳐 안전하게 API를 호출합니다."
          title="요청 작성"
        >
          <div className="flex flex-wrap items-end gap-3">
            <FormSelect
              aria-label="HTTP method"
              className="w-32"
              id="api-method"
              label="요청 방식"
              reserveMessage={false}
              value={method}
              onChange={(event) => setMethod(event.target.value as "GET" | "POST")}
            >
              <option>GET</option>
              <option>POST</option>
            </FormSelect>
            <FormField
              aria-label="API path"
              className="min-w-0 flex-1 basis-72"
              id="api-path"
              label="요청 경로"
              placeholder="/v1/weather/locations"
              reserveMessage={false}
              value={path}
              onChange={(event) => setPath(event.target.value)}
            />
            <Button loading={running} type="button" onClick={runRequest}>
              {running ? "실행 중…" : "요청 실행"}
            </Button>
          </div>

          {method === "POST" ? (
            <FormTextArea
              aria-label="JSON body"
              id="api-body"
              label="요청 본문(JSON)"
              placeholder={'{"location_id":"seoul"}'}
              reserveMessage={false}
              rows={6}
              value={body}
              onChange={(event) => setBody(event.target.value)}
            />
          ) : null}

          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2 text-xs font-medium text-text-secondary">
              <span>응답 결과</span>
              <span>{result === null ? "—" : "JSON"}</span>
            </div>
            {result === null ? (
              <EmptyState title="요청 결과가 여기에 표시됩니다." />
            ) : (
              <JsonViewer aria-label="응답 결과" copyable maxHeight="lg" value={result} />
            )}
          </div>
        </SectionCard>
      </div>
    </AdminShell>
  );
}
