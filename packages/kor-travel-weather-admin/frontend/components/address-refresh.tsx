"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { dagsterRunUrl } from "@/lib/dagster-scope";
import styles from "./address-refresh.module.css";

import type { AddressRefreshSummary } from "@/lib/api";

const date = (value?: string | null) => value ? new Date(value).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "미확인";

export function AddressRefresh() {
  const [summary, setSummary] = useState<AddressRefreshSummary | null>(null);
  const [loadError, setLoadError] = useState("");
  const [launchError, setLaunchError] = useState("");
  const [notice, setNotice] = useState("");
  const [runId, setRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const requestId = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    try {
      const response = await fetch("/api/address-refresh", { cache: "no-store", signal: current.signal });
      if (!response.ok) throw new Error("주소 갱신 상태를 읽지 못했습니다.");
      const body = await response.json();
      if (!current.signal.aborted) { setSummary(body); setLoadError(""); }
    } catch {
      if (!current.signal.aborted) setLoadError("주소 갱신 상태를 읽지 못했습니다. 상태 새로고침으로 재시도하세요.");
    }
  }, []);
  useEffect(() => { void load(); return () => controller.current?.abort(); }, [load]);
  async function launch() {
    if (busy) return;
    setBusy(true); setLaunchError(""); setNotice("");
    requestId.current ??= crypto.randomUUID();
    try {
      const response = await fetch("/api/address-refresh", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ requestId: requestId.current }), signal: AbortSignal.timeout(35_000),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "갱신 실행을 예약하지 못했습니다.");
      setRunId(body.runId);
      requestId.current = null;
      if (body.status === "FAILURE" || body.status === "CANCELED") {
        setLaunchError(body.status === "FAILURE"
          ? "기존 요청의 실행이 실패했습니다. 실행 기록을 확인한 뒤 새 수동 요청으로 재시도할 수 있습니다."
          : "기존 요청의 실행이 취소됐습니다. 실행 기록을 확인한 뒤 새 수동 요청으로 재시도할 수 있습니다.");
      } else if (body.status === "SUCCESS") {
        setNotice("이 요청의 실행이 정상 완료되었습니다. 아래 실행 기록에서 결과를 확인하세요.");
      } else if (body.replayed) {
        setNotice("기존 요청의 실행을 확인했습니다. 아래 실행 기록에서 현재 상태를 확인하세요. Daemon이 중지되어 있으면 대기합니다.");
      } else {
        setNotice("실행을 예약했습니다. 주소 갱신 완료 여부는 아래 Dagster 실행 상태에서 확인하세요. Daemon이 중지되어 있으면 대기합니다.");
      }
      if (body.status !== "FAILURE" && body.status !== "CANCELED") void load();
    } catch (reason) {
      setLaunchError(reason instanceof Error ? reason.message : "예약 결과를 확인하지 못했습니다. 다시 누르면 같은 요청의 예약 여부를 확인합니다.");
    } finally { setBusy(false); }
  }
  return (
    <section aria-label="주소 갱신" className={"panel " + styles.panel}>
      <h2>주소 갱신</h2>
      <p>VWorld 우선 · Kakao 보조로 좌표의 최신 주소를 확인합니다. 위치별 마지막 확인일부터 달력 6개월마다 갱신하며, 원천 주소와 관리자 좌표는 보존합니다.</p>
      {summary ? <>
        <p>전체 {summary.total.toLocaleString()}개 · 갱신 대상 {summary.due.toLocaleString()}개 · 실패 {summary.failed.toLocaleString()}개 · 자동 갱신 {summary.auto_enabled ? "켜짐" : "꺼짐"}</p>
        <p>최근 정상 확인: {date(summary.last_resolved_at)}{summary.in_progress ? " · 배치 진행 또는 재개 대기" : ""}</p>
        {!summary.configured ? <p role="status">VWorld 또는 Kakao 서버 키를 설정해야 실행할 수 있습니다.</p> : null}
      </> : <p role="status">주소 갱신 상태 확인 중…</p>}
      <div className={styles.actions}>
        <button type="button" disabled={busy || !summary?.configured} onClick={() => void launch()}>{busy ? "예약 중…" : "전체 주소 수동 갱신"}</button>
        <button type="button" onClick={() => void load()}>상태 새로고침</button>
        {runId ? <a href={dagsterRunUrl(runId)} target="_blank" rel="noreferrer">실행 기록 보기 ↗</a> : null}
      </div>
      {loadError ? <p role="alert">{loadError}</p> : null}
      {launchError ? <p role="alert">{launchError}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {summary?.items.length ? <>
        <p>최근 확인한 위치 최대 50개입니다. 실패·기한 경과 주소는 최신으로 보장되지 않습니다. 작은 화면에서는 표를 좌우로 이동해 확인하세요.</p>
        <div className={styles.scroll} role="region" aria-label="최근 주소 확인 결과" tabIndex={0}>
        <table className={styles.table}>
          <caption>최근 주소 확인 결과</caption>
          <thead><tr><th>위치</th><th>확인 주소</th><th>상태 / 제공자</th><th>확인일 / 다음 갱신일</th></tr></thead>
          <tbody>{summary.items.map(item => <tr key={item.target_key}>
            <td>{item.name || item.target_key}</td><td>{item.address?.full || "주소 없음"}</td>
            <td>{item.stale ? "재확인 필요" : "정상"} · {item.status} / {item.provider || "미확인"}{item.error_code ? " (" + item.error_code + ")" : ""}</td>
            <td>{date(item.resolved_at)}<br />{date(item.next_refresh_at)}</td>
          </tr>)}</tbody>
        </table>
      </div></> : null}
    </section>
  );
}
