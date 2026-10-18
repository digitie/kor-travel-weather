import { AddressRefresh } from "@/components/address-refresh";
"use client";

import { DagsterOperations } from "@kor-travel/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/admin-shell";
import { type DagsterSnapshot, type DagsterRunFailureDetail, getDagsterSnapshot, getDagsterRunFailure, jobLabel, runStatusLabel } from "@/lib/dagster";
import { dagsterLocationUrl, dagsterRunUrl, dagsterScheduleUrl } from "@/lib/dagster-scope";

export default function DagsterPage() {
  const [snapshot, setSnapshot] = useState<DagsterSnapshot | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ runId: string; loading: boolean; result?: DagsterRunFailureDetail; error?: string } | null>(null);
  const requestVersion = useRef(0);
  const detailVersion = useRef(0);
  const overviewRequest = useRef<AbortController | null>(null);
  const detailRequest = useRef<{ runId: string; controller: AbortController } | null>(null);
  const load = useCallback(() => {
    const version = ++requestVersion.current;
    overviewRequest.current?.abort();
    detailRequest.current?.controller.abort();
    ++detailVersion.current;
    detailRequest.current = null;
    setSelectedRunId(null);
    setDetail(null);
    const controller = new AbortController();
    overviewRequest.current = controller;
    setLoading(true);
    setError("");
    getDagsterSnapshot(12, controller.signal)
      .then((next) => { if (version === requestVersion.current) setSnapshot(next); })
      .catch((reason: unknown) => {
        if (version === requestVersion.current && !controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Dagster 상태를 읽지 못했습니다.");
        }
      })
      .finally(() => {
        if (version === requestVersion.current) {
          overviewRequest.current = null;
          setLoading(false);
        }
      });
  }, []);

  const selectRun = useCallback((runId: string) => {
    if (overviewRequest.current !== null || !snapshot?.runs.some(run => run.runId === runId)) return;
    if (detailRequest.current?.runId === runId) return;
    detailRequest.current?.controller.abort();
    detailRequest.current = null;
    const version = ++detailVersion.current;
    const overviewVersion = requestVersion.current;
    setSelectedRunId(runId);
    if (snapshot.runs.find(run => run.runId === runId)?.status !== "FAILURE") { setDetail(null); return; }
    const controller = new AbortController();
    detailRequest.current = { runId, controller };
    setDetail({ runId, loading: true });
    getDagsterRunFailure(runId, controller.signal)
      .then(result => {
        if (version === detailVersion.current && overviewVersion === requestVersion.current && !controller.signal.aborted) {
          setDetail({ runId, loading: false, result });
        }
      })
      .catch((reason: unknown) => {
        if (version === detailVersion.current && overviewVersion === requestVersion.current && !controller.signal.aborted) {
          setDetail({ runId, loading: false, error: reason instanceof Error && reason.message ? reason.message : "실패 원인을 조회하지 못했습니다." });
        }
      })
      .finally(() => { if (version === detailVersion.current) detailRequest.current = null; });
  }, [snapshot]);
  useEffect(() => {
    const sequence = requestVersion;
    const detailSequence = detailVersion;
    const overviewController = overviewRequest;
    const detailController = detailRequest;
    load();
    return () => {
      ++sequence.current;
      ++detailSequence.current;
      overviewController.current?.abort();
      detailController.current?.controller.abort();
    };
  }, [load]);

  const displaySnapshot = snapshot && { ...snapshot, runs: snapshot.runs.map(run => ({
    ...run,
    errorMessage: run.status !== "FAILURE" ? run.errorMessage : detail?.runId !== run.runId
      ? "실패 원인 미조회 · 실행을 선택해 조회하세요."
      : detail.loading ? "실패 원인 조회 중…" : detail.error ? "실패 원인 조회 실패 · 상세에서 다시 조회하세요."
      : detail.result?.message ?? (detail.result?.complete ? "실패 이벤트가 없습니다." : "조회 상한에 도달했습니다."),
  })) };

  return (
    <>
      <PageHeader
        description="매시간 날씨를 자동으로 가져오는 작업이 잘 돌고 있는지 보여줍니다. 실패하거나 멈춘 작업을 여기서 확인하세요."
        section="시스템"
        title="Dagster 운영"
      />
      <AddressRefresh />
      <DagsterOperations snapshot={displaySnapshot} error={error} loading={loading} onRefresh={load}
        showRunDetails selectedRunId={selectedRunId} onSelectRun={selectRun}
        renderRunDetail={run => !run ? <p className="empty">실행을 선택하면 상세를 확인할 수 있습니다.</p> : (
          <div>
            <h3>{jobLabel(run.jobName)}</h3><code>{run.runId}</code><p>{runStatusLabel(run.status)}</p>
            {run.status === "FAILURE" ? (
              <div aria-busy={detail?.loading ?? false}>
                {detail?.runId !== run.runId ? <p>실패 원인 미조회</p> : detail.loading ? <p role="status">실패 원인 조회 중…</p> : detail.error ? (
                  <div role="alert"><p>{detail.error}</p><button type="button" onClick={() => selectRun(run.runId)}>실패 원인 다시 조회</button></div>
                ) : <><p className="dagster-run-error">{detail.result?.message ?? (detail.result?.complete ? "실패 이벤트가 없습니다." : "조회 범위에서 실패 원인을 찾지 못했습니다.")}</p>
                  {!detail.result?.complete ? <p>조회 상한에 도달했습니다. 전체 로그는 Dagster에서 확인하세요.</p> : null}</>}
              </div>
            ) : <p>실패하지 않은 실행은 오류 로그를 자동 조회하지 않습니다.</p>}
            <a className="inline-link" href={dagsterRunUrl(run.runId)} target="_blank" rel="noreferrer">선택한 실행을 Dagster에서 열기 ↗</a>
          </div>
        )}
        jobLabel={jobLabel} runUrl={dagsterRunUrl} scheduleUrl={dagsterScheduleUrl} locationUrl={dagsterLocationUrl()} />
    </>
  );
}
