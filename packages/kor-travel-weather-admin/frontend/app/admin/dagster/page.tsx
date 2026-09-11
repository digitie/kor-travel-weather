"use client";

import { ExternalLink, RefreshCw, Workflow } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import { DagsterRepository, DagsterRun, DagsterSnapshot, STALLED_RUN_THRESHOLD_SECONDS, formatElapsed, getDagsterSnapshot, isStalledRun, jobLabel, runElapsedSeconds } from "@/lib/dagster";

function statusClass(status: string | null | undefined) {
  if (status === "RUNNING" || status === "SUCCESS" || status === "STARTED") return "on";
  if (status === "FAILURE" || status === "CANCELED" || status === "STOPPED") return "off";
  return "warn";
}

// A STARTED run reads identically to a healthy one until an operator does the
// arithmetic on its start time themselves -- which is exactly the state that
// held ingestion down for hours before anyone noticed. Once it crosses
// isStalledRun's threshold this overrides the badge to "warn" instead.
function runStatusClass(run: DagsterRun, nowSeconds: number) {
  if (isStalledRun(run, nowSeconds)) return "warn";
  return statusClass(run.status);
}

function dateTime(epoch: number | null) {
  return epoch ? new Date(epoch * 1000).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }) : "—";
}

function RepositoryCard({ repository }: { repository: DagsterRepository }) {
  return <div className="dagster-item"><div className="dagster-item-head"><strong>{repository.name}</strong><span className="status on">loaded</span></div><p>{repository.locationName}</p><div className="dagster-item-meta"><span>{repository.assets.length} assets</span><span>{repository.jobs.length} jobs</span></div><div className="dagster-tags">{repository.jobs.map((job) => <code key={job} title={job}>{jobLabel(job)}</code>)}</div>{repository.schedules.map((schedule) => <div className="schedule-row" key={schedule.name}><span><Workflow size={14} />{schedule.name}</span><span className={`status ${statusClass(schedule.status)}`}>{schedule.status ?? "unknown"}</span><small>{schedule.cron ?? "manual"}</small></div>)}</div>;
}

function RunRow({ run, nowSeconds }: { run: DagsterRun; nowSeconds: number }) {
  const elapsed = runElapsedSeconds(run, nowSeconds);
  const stalled = isStalledRun(run, nowSeconds);
  return (
    <tr>
      <td>
        <span className={`status ${runStatusClass(run, nowSeconds)}`}>{run.status}</span>
        {elapsed !== null ? <small className="dagster-run-elapsed">{formatElapsed(elapsed)} 경과{stalled ? " · 정체 의심" : ""}</small> : null}
      </td>
      <td>
        <strong title={run.jobName}>{jobLabel(run.jobName)}</strong>
        <code>{run.runId.slice(0, 14)}…</code>
        {run.status === "FAILURE" ? <small className="dagster-run-error">{run.errorMessage ?? "실패 원인을 불러오지 못했습니다."}</small> : null}
      </td>
      <td>{dateTime(run.startTime)}</td>
      <td>{dateTime(run.endTime)}</td>
      <td><a className="inline-link" href={`${process.env.NEXT_PUBLIC_DAGSTER_URL ?? "https://weather-dagster.digitie.mywire.org"}/runs/${encodeURIComponent(run.runId)}`} target="_blank" rel="noreferrer">Dagster에서 열기 <ExternalLink size={13} /></a></td>
    </tr>
  );
}

export default function DagsterPage() {
  const [snapshot, setSnapshot] = useState<DagsterSnapshot | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = useCallback(() => {
    setLoading(true);
    setError("");
    getDagsterSnapshot().then(setSnapshot).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Dagster 상태를 읽지 못했습니다.")).finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const schedules = snapshot?.repositories.flatMap((repository) => repository.schedules) ?? [];
  const healthy = schedules.filter((schedule) => schedule.status === "RUNNING").length;
  const successes = snapshot?.runs.filter((run) => run.status === "SUCCESS").length ?? 0;
  const failures = snapshot?.runs.filter((run) => run.status === "FAILURE").length ?? 0;
  // The snapshot's own fetch time, not the render clock: elapsed durations
  // then describe exactly what the data on screen showed, and remain correct
  // even if this tab sits open a while before the next refresh.
  const nowSeconds = snapshot ? new Date(snapshot.checkedAt).getTime() / 1000 : Date.now() / 1000;
  const stalled = snapshot?.runs.filter((run) => isStalledRun(run, nowSeconds)).length ?? 0;

  return (
    <>
      <PageHeader
        actions={
          <>
            <button className="secondary" type="button" onClick={load} disabled={loading}>
              <RefreshCw size={15} className={loading ? "spin" : ""} />
              새로고침
            </button>
            <a className="button secondary" href={process.env.NEXT_PUBLIC_DAGSTER_URL ?? "https://weather-dagster.digitie.mywire.org"} target="_blank" rel="noreferrer">
              Dagster UI <ExternalLink size={15} />
            </a>
          </>
        }
        description="kor-travel-map 운영 화면과 같은 방식으로 repository, schedule, 최근 실행을 한눈에 확인합니다."
        section="시스템"
        title="Dagster 운영"
      />
      {error ? <div className="error" role="alert">{error} <button type="button" className="ghost" onClick={load}>다시 시도</button></div> : null}
      {stalled > 0 ? (
        <div className="error" role="alert">
          {stalled}개 실행이 {Math.floor(STALLED_RUN_THRESHOLD_SECONDS / 60)}분 넘게 STARTED 상태입니다 -- 죽은 프로세스가 동시 실행 슬롯을 계속 붙잡고 있을 수 있습니다. 아래 목록에서 &quot;정체 의심&quot; 표시를 확인하세요.
        </div>
      ) : null}
      <section className="ops-grid" aria-label="Dagster 요약">
        <div className="panel ops-card"><span>repositories</span><strong>{snapshot?.repositories.length ?? "—"}</strong><small>loaded code locations</small></div>
        <div className="panel ops-card"><span>active schedules</span><strong>{snapshot ? `${healthy}/${schedules.length}` : "—"}</strong><small>RUNNING schedule</small></div>
        <div className="panel ops-card"><span>recent success</span><strong>{snapshot ? successes : "—"}</strong><small>last {snapshot?.runs.length ?? 0} runs</small></div>
        <div className="panel ops-card"><span>recent failure</span><strong>{snapshot ? failures : "—"}</strong><small>재시도·원인 확인 대상</small></div>
        <div className="panel ops-card"><span>정체된 실행</span><strong className={stalled > 0 ? "warn-text" : undefined}>{snapshot ? stalled : "—"}</strong><small>{Math.floor(STALLED_RUN_THRESHOLD_SECONDS / 60)}분 이상 STARTED</small></div>
      </section>
      <section className="dagster-layout">
        <div className="panel"><div className="panel-head"><div><h2>Repository & schedules</h2><p>hourly KMA 및 external provider orchestration</p></div></div><div className="dagster-list">{snapshot?.repositories.map((repository) => <RepositoryCard key={`${repository.locationName}:${repository.name}`} repository={repository} />) ?? <div className="loading-block">Dagster 상태를 불러오는 중…</div>}</div></div>
        <div className="panel dagster-runs"><div className="panel-head"><div><h2>최근 실행</h2><p>{snapshot ? `마지막 확인 ${new Date(snapshot.checkedAt).toLocaleTimeString("ko-KR")}` : "실행 기록을 불러오는 중…"}</p></div></div>{snapshot?.runs.length ? <div className="table-wrap"><table><thead><tr><th scope="col">status</th><th scope="col">job</th><th scope="col">started</th><th scope="col">finished</th><th scope="col" /></tr></thead><tbody>{snapshot.runs.map((run) => <RunRow key={run.runId} run={run} nowSeconds={nowSeconds} />)}</tbody></table></div> : <div className="empty">최근 Dagster 실행이 없습니다.</div>}</div>
      </section>
    </>
  );
}
