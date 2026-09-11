"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import {
  getProviders,
  getSyncRunSources,
  getSyncRuns,
  syncRunStatusLabel,
  Provider,
  SourceRecordSummary,
  SyncRun,
} from "@/lib/api";

function statusClass(status: string) {
  if (status === "success") return "on";
  if (status === "running") return "warn";
  return "off";
}

function timestamp(value: string | null) {
  return value ? new Date(value).toLocaleString("ko-KR") : "—";
}

function providerLabel(providers: Map<string, Provider>, providerKey: string) {
  return providers.get(providerKey)?.label ?? providerKey;
}

function datasetLabel(providers: Map<string, Provider>, providerKey: string, datasetKey: string) {
  return providers.get(providerKey)?.datasets.find((dataset) => dataset.key === datasetKey)?.label ?? datasetKey;
}

function RunDetail({ run, providers }: { run: SyncRun; providers: Map<string, Provider> }) {
  const [sources, setSources] = useState<SourceRecordSummary[] | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [showSources, setShowSources] = useState(false);

  async function loadSources() {
    if (sources !== null) {
      setShowSources((current) => !current);
      return;
    }
    setShowSources(true);
    try {
      setSources((await getSyncRunSources(run.run_id)).data);
    } catch (reason) {
      setSourcesError(reason instanceof Error ? reason.message : "원본 데이터를 불러오지 못했습니다.");
      setSources([]);
    }
  }

  return (
    <tr className="sync-run-detail-row">
      <td colSpan={7}>
        <div className="sync-run-detail">
          {run.error ? (
            <div>
              <div className="eyebrow">실패 원인</div>
              <p className="error-cell">{run.error}</p>
            </div>
          ) : null}
          <div className="sync-run-detail-grid">
            <div><span>실행 ID</span><code>{run.run_id}</code></div>
            <div><span>마지막 응답</span><code>{timestamp(run.heartbeat_at)}</code></div>
            <div><span>종료 시각</span><code>{timestamp(run.finished_at)}</code></div>
            <div><span>대상 위치</span><code>{run.locations_total}</code></div>
            <div><span>격자 수</span><code>{run.grids_fetched}</code></div>
            <div><span>중기예보 그룹 수</span><code>{run.mid_groups_fetched}</code></div>
            <div><span>요청 수</span><code>{run.requests_fetched}</code></div>
            <div><span>저장된 값 수</span><code>{run.values_loaded}</code></div>
          </div>
          <div>
            <button className="ghost" type="button" onClick={() => void loadSources()}>
              {showSources ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              원본 데이터 {sources ? `(${sources.length}건)` : ""}
            </button>
            {showSources ? (
              sourcesError ? (
                <div className="error" role="alert">{sourcesError}</div>
              ) : sources === null ? (
                <div className="loading" aria-busy="true">불러오는 중…</div>
              ) : sources.length === 0 ? (
                <div className="empty">연결된 원본 데이터가 없습니다.</div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th scope="col">원본 ID</th><th scope="col">데이터셋</th><th scope="col">대상</th><th scope="col">행 수</th><th scope="col">가져온 시각</th></tr>
                    </thead>
                    <tbody>
                      {sources.map((source) => (
                        <tr key={source.source_record_key}>
                          <td><code>{source.source_record_key}</code></td>
                          <td title={source.dataset_key}>{datasetLabel(providers, source.provider, source.dataset_key)}</td>
                          <td><code>{source.source_entity_id}</code></td>
                          <td>{source.row_count ?? "—"}</td>
                          <td><code>{timestamp(source.fetched_at)}</code></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            ) : null}
          </div>
        </div>
      </td>
    </tr>
  );
}

function RunRow({ run, providers, expanded, onToggle }: { run: SyncRun; providers: Map<string, Provider>; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className={expanded ? "sync-run-row-expanded" : undefined}>
        <td>
          <button className="ghost row-expand-toggle" type="button" onClick={onToggle} aria-expanded={expanded}>
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <code>{run.run_id.slice(0, 8)}…</code>
          </button>
        </td>
        <td><strong title={run.provider}>{providerLabel(providers, run.provider)}</strong></td>
        <td title={run.dataset_key}>{datasetLabel(providers, run.provider, run.dataset_key)}</td>
        <td><span className={`status ${statusClass(run.status)}`}>{syncRunStatusLabel(run.status)}</span></td>
        <td><code>{timestamp(run.started_at)}</code></td>
        <td>{run.values_loaded}</td>
        <td className="error-cell">{run.error ? (run.error.length > 60 ? `${run.error.slice(0, 60)}…` : run.error) : "—"}</td>
      </tr>
      {expanded ? <RunDetail run={run} providers={providers} /> : null}
    </>
  );
}

export default function SyncRunsPage() {
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [providers, setProviders] = useState<Map<string, Provider>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  useEffect(() => {
    getSyncRuns()
      .then((result) => setRuns(result.data))
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "실행 이력을 불러오지 못했습니다."),
      );
    getProviders()
      .then((result) => setProviders(new Map(result.data.map((provider) => [provider.provider, provider]))))
      .catch(() => undefined);
  }, []);

  return (
    <>
      <PageHeader
        description="매시간 날씨 데이터를 가져온 결과입니다. 실패한 항목은 표에서 바로 보이고, 행을 펼치면 실패 원인과 어떤 원본 데이터를 가져왔는지 확인할 수 있습니다."
        section="수집 파이프라인"
        title="수집 실행"
      />
      <section className="panel">
        {error ? <div className="error" role="alert">{error}</div> : null}
        {runs.length === 0 ? (
          <div className="empty">아직 수집 실행이 없습니다.</div>
        ) : (
          <div className="table-wrap">
          <table>
            <thead>
              <tr><th scope="col">실행</th><th scope="col">제공처</th><th scope="col">데이터셋</th><th scope="col">상태</th><th scope="col">시작</th><th scope="col">값 수</th><th scope="col">오류</th></tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <RunRow
                  key={run.run_id}
                  run={run}
                  providers={providers}
                  expanded={expandedRun === run.run_id}
                  onToggle={() => setExpandedRun((current) => (current === run.run_id ? null : run.run_id))}
                />
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>
    </>
  );
}
