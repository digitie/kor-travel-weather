"use client";

import { useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import {
  getSyncRunSources,
  getSyncRuns,
  SourceRecordSummary,
  SyncRun,
} from "@/lib/api";

export default function SyncRunsPage() {
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [sources, setSources] = useState<SourceRecordSummary[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(false);

  useEffect(() => {
    getSyncRuns()
      .then((result) => setRuns(result.data))
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "실행 이력을 불러오지 못했습니다."),
      );
  }, []);

  async function inspectSources(runId: string) {
    if (selectedRun === runId) {
      setSelectedRun(null);
      return;
    }
    setSelectedRun(runId);
    setSourcesLoading(true);
    try {
      setSources((await getSyncRunSources(runId)).data);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "source lineage를 불러오지 못했습니다.");
      setSources([]);
    } finally {
      setSourcesLoading(false);
    }
  }

  return (
    <>
      <PageHeader
        description="Dagster hourly asset의 성공·실패와 publish 결과입니다."
        section="Operations"
        title="수집 실행"
      />
      <section className="panel">
        {error ? <div className="error" role="alert">{error}</div> : null}
        {runs.length === 0 ? (
          <div className="empty">아직 수집 실행이 없습니다.</div>
        ) : (
          <table>
            <thead>
              <tr><th>run</th><th>status</th><th>started</th><th>grids</th><th>mid groups</th><th>requests</th><th>facts</th><th>error</th><th /></tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id}>
                  <td><code>{run.run_id}</code></td>
                  <td><span className={`status ${run.status === "success" ? "on" : "off"}`}>{run.status}</span></td>
                  <td><code>{new Date(run.started_at).toLocaleString("ko-KR")}</code></td>
                  <td>{run.grids_fetched}</td>
                  <td>{run.mid_groups_fetched}</td>
                  <td>{run.requests_fetched}</td>
                  <td>{run.values_loaded}</td>
                  <td className="error-cell">{run.error ?? "—"}</td>
                  <td><button className="secondary" type="button" onClick={() => void inspectSources(run.run_id)}>{selectedRun === run.run_id ? "닫기" : "source"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      {selectedRun ? (
        <section className="panel detail-panel">
          <div className="panel-head"><div><div className="eyebrow">lineage</div><h2>{selectedRun} sources</h2><p>원문 rows는 노출하지 않고 redacted metadata만 표시합니다.</p></div></div>
          {sourcesLoading ? <div className="loading" aria-busy="true">source lineage를 불러오는 중…</div> : sources.length === 0 ? <div className="empty">연결된 source record가 없습니다.</div> : <table><thead><tr><th>source key</th><th>dataset</th><th>entity</th><th>rows</th><th>fetched</th></tr></thead><tbody>{sources.map((source) => <tr key={source.source_record_key}><td><code>{source.source_record_key}</code></td><td>{source.dataset_key}</td><td><code>{source.source_entity_id}</code></td><td>{source.row_count ?? "—"}</td><td><code>{new Date(source.fetched_at).toLocaleString("ko-KR")}</code></td></tr>)}</tbody></table>}
        </section>
      ) : null}
    </>
  );
}
