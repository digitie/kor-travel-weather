"use client";

import { ChevronDown, ChevronRight, ExternalLink, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import { DagsterRepository, DagsterRun, DagsterSchedule, DagsterSnapshot, STALLED_RUN_THRESHOLD_SECONDS, describeCron, formatElapsed, getDagsterSnapshot, isStalledRun, jobLabel, runElapsedSeconds, runStatusLabel } from "@/lib/dagster";

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

function RunRow({ run, nowSeconds }: { run: DagsterRun; nowSeconds: number }) {
  const elapsed = runElapsedSeconds(run, nowSeconds);
  const stalled = isStalledRun(run, nowSeconds);
  return (
    <tr>
      <td>
        <span className={`status ${runStatusClass(run, nowSeconds)}`}>{runStatusLabel(run.status)}</span>
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

function ScheduleRow({ schedule, expanded, onToggle }: { schedule: DagsterSchedule; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr>
        <td>
          <button className="ghost row-expand-toggle" type="button" onClick={onToggle} aria-expanded={expanded}>
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {jobLabel(schedule.jobName)}
          </button>
        </td>
        <td>{schedule.cron ? describeCron(schedule.cron) : "수동 실행"}</td>
        <td><span className={`status ${statusClass(schedule.status)}`}>{schedule.status === "RUNNING" ? "사용" : "중지"}</span></td>
      </tr>
      {expanded ? (
        <tr className="sync-run-detail-row">
          <td colSpan={3}>
            <div className="sync-run-detail-grid">
              <div><span>실행되는 작업</span><code>{schedule.jobName}</code></div>
              <div><span>스케줄 이름</span><code>{schedule.name}</code></div>
              <div><span>실행 주기(원본)</span><code>{schedule.cron ?? "—"}</code></div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

export default function DagsterPage() {
  const [snapshot, setSnapshot] = useState<DagsterSnapshot | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [expandedSchedule, setExpandedSchedule] = useState<string | null>(null);
  const load = useCallback(() => {
    setLoading(true);
    setError("");
    getDagsterSnapshot().then(setSnapshot).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Dagster 상태를 읽지 못했습니다.")).finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const schedules = snapshot?.repositories.flatMap((repository: DagsterRepository) => repository.schedules) ?? [];
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
        description="매시간 날씨를 자동으로 가져오는 작업이 잘 돌고 있는지 보여줍니다. 실패하거나 멈춘 작업을 여기서 확인하세요."
        section="시스템"
        title="Dagster 운영"
      />
      {error ? <div className="error" role="alert">{error} <button type="button" className="ghost" onClick={load}>다시 시도</button></div> : null}
      {stalled > 0 ? (
        <div className="error" role="alert">
          {stalled}개 실행이 {Math.floor(STALLED_RUN_THRESHOLD_SECONDS / 60)}분 넘게 실행 중 상태입니다 -- 멈춘 작업이 자리를 계속 차지하고 있을 수 있습니다. 아래 목록에서 &quot;정체 의심&quot; 표시를 확인하세요.
        </div>
      ) : null}
      <section className="ops-grid" aria-label="Dagster 요약">
        <div className="panel ops-card"><span>사용 중인 스케줄</span><strong>{snapshot ? `${healthy}/${schedules.length}` : "—"}</strong><small>전체 스케줄 대비</small></div>
        <div className="panel ops-card"><span>최근 성공</span><strong>{snapshot ? successes : "—"}</strong><small>최근 {snapshot?.runs.length ?? 0}건 중</small></div>
        <div className="panel ops-card"><span>최근 실패</span><strong>{snapshot ? failures : "—"}</strong><small>재시도·원인 확인 대상</small></div>
        <div className="panel ops-card"><span>정체된 실행</span><strong className={stalled > 0 ? "warn-text" : undefined}>{snapshot ? stalled : "—"}</strong><small>{Math.floor(STALLED_RUN_THRESHOLD_SECONDS / 60)}분 이상 실행 중</small></div>
      </section>
      <section className="panel dagster-runs">
        <div className="panel-head"><div><h2>최근 실행 · 마지막 확인 {snapshot ? new Date(snapshot.checkedAt).toLocaleTimeString("ko-KR") : "불러오는 중…"}</h2></div></div>
        {snapshot?.runs.length ? <div className="table-wrap"><table><thead><tr><th scope="col">상태</th><th scope="col">작업</th><th scope="col">시작</th><th scope="col">종료</th><th scope="col" /></tr></thead><tbody>{snapshot.runs.map((run) => <RunRow key={run.runId} run={run} nowSeconds={nowSeconds} />)}</tbody></table></div> : <div className="empty">{snapshot ? "최근 Dagster 실행이 없습니다." : "실행 기록을 불러오는 중…"}</div>}
      </section>
      <section className="panel">
        <div className="panel-head"><div><h2>스케줄</h2></div></div>
        {schedules.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">작업</th><th scope="col">주기</th><th scope="col">상태</th></tr></thead>
              <tbody>
                {schedules.map((schedule) => (
                  <ScheduleRow
                    key={schedule.name}
                    schedule={schedule}
                    expanded={expandedSchedule === schedule.name}
                    onToggle={() => setExpandedSchedule((current) => (current === schedule.name ? null : schedule.name))}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">{snapshot ? "등록된 스케줄이 없습니다." : "스케줄을 불러오는 중…"}</div>
        )}
      </section>
    </>
  );
}
