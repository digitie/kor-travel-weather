"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import {
  getSyncRunSources,
  getSyncRuns,
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

function RunDetail({ run }: { run: SyncRun }) {
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
      setSourcesError(reason instanceof Error ? reason.message : "source lineage를 불러오지 못했습니다.");
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
            <div><span>run id</span><code>{run.run_id}</code></div>
            <div><span>heartbeat</span><code>{timestamp(run.heartbeat_at)}</code></div>
            <div><span>finished</span><code>{timestamp(run.finished_at)}</code></div>
            <div><span>대상 위치</span><code>{run.locations_total}</code></div>
            <div><span>grids</span><code>{run.grids_fetched}</code></div>
            <div><span>mid groups</span><code>{run.mid_groups_fetched}</code></div>
            <div><span>requests</span><code>{run.requests_fetched}</code></div>
            <div><span>facts</span><code>{run.values_loaded}</code></div>
          </div>
          <div>
            <button className="ghost" type="button" onClick={() => void loadSources()}>
              {showSources ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              source lineage {sources ? `(${sources.length})` : ""}
            </button>
            {showSources ? (
              sourcesError ? (
                <div className="error" role="alert">{sourcesError}</div>
              ) : sources === null ? (
                <div className="loading" aria-busy="true">불러오는 중…</div>
              ) : sources.length === 0 ? (
                <div className="empty">연결된 source record가 없습니다.</div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th scope="col">source key</th><th scope="col">dataset</th><th scope="col">entity</th><th scope="col">rows</th><th scope="col">fetched</th></tr>
                    </thead>
                    <tbody>
                      {sources.map((source) => (
                        <tr key={source.source_record_key}>
                          <td><code>{source.source_record_key}</code></td>
                          <td>{source.dataset_key}</td>
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

function RunRow({ run, expanded, onToggle }: { run: SyncRun; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className={expanded ? "sync-run-row-expanded" : undefined}>
        <td>
          <button className="ghost row-expand-toggle" type="button" onClick={onToggle} aria-expanded={expanded}>
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <code>{run.run_id.slice(0, 8)}…</code>
          </button>
        </td>
        <td><strong>{run.provider}</strong></td>
        <td>{run.dataset_key}</td>
        <td><span className={`status ${statusClass(run.status)}`}>{run.status}</span></td>
        <td><code>{timestamp(run.started_at)}</code></td>
        <td>{run.values_loaded}</td>
        <td className="error-cell">{run.error ? (run.error.length > 60 ? `${run.error.slice(0, 60)}…` : run.error) : "—"}</td>
      </tr>
      {expanded ? <RunDetail run={run} /> : null}
    </>
  );
}

export default function SyncRunsPage() {
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  useEffect(() => {
    getSyncRuns()
      .then((result) => setRuns(result.data))
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "실행 이력을 불러오지 못했습니다."),
      );
  }, []);

  return (
    <>
      <PageHeader
        description="Dagster hourly asset의 성공·실패와 publish 결과입니다. 실패한 provider/dataset을 표에서 바로 확인하고, 행을 펼쳐 원인과 lineage를 봅니다."
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
              <tr><th scope="col">run</th><th scope="col">provider</th><th scope="col">dataset</th><th scope="col">status</th><th scope="col">started</th><th scope="col">facts</th><th scope="col">error</th></tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <RunRow
                  key={run.run_id}
                  run={run}
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
