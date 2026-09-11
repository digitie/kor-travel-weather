"use client";

import { Activity, CloudSun, Database, MapPin, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import { getHealth, getLocations, getPublicLocations, getSyncRuns, syncRunStatusLabel, SyncRun } from "@/lib/api";

function SummaryCard({
  icon: Icon,
  label,
  value,
  detail,
  loading,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  detail: string;
  loading: boolean;
}) {
  return (
    <div className="panel card summary-card">
      <div className="summary-card-head">
        <span>{label}</span>
        <span className="summary-card-icon"><Icon size={18} aria-hidden="true" /></span>
      </div>
      <strong>{loading ? <span className="skeleton skeleton-value" aria-label="불러오는 중" /> : value}</strong>
      <small>{loading ? <span className="skeleton skeleton-line" aria-hidden="true" /> : detail}</small>
    </div>
  );
}

export default function HomePage() {
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [health, setHealth] = useState("확인 중");
  const [error, setError] = useState<string | null>(null);
  const [catalogTotal, setCatalogTotal] = useState(0);
  const [activeTotal, setActiveTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [locationResult, activeResult, runResult, healthResult] = await Promise.all([
        getLocations(undefined, 1),
        getPublicLocations(1),
        getSyncRuns(10),
        getHealth(),
      ]);
      setCatalogTotal(locationResult.meta.page?.total ?? locationResult.data.length);
      setRuns(runResult.data);
      setHealth(healthResult.status === "ok" ? "정상" : healthResult.status);
      setActiveTotal(activeResult.meta.page?.total ?? activeResult.data.length);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "상태를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const lastRun = runs[0];
  return (
    <>
      <PageHeader
        actions={
          <>
            <button className="button secondary" disabled={loading} onClick={() => void refresh()} type="button">
              <RefreshCw size={15} className={loading ? "spin" : ""} /> 새로고침
            </button>
            <Link className="button secondary" href="/weather">날씨 지도</Link>
            <Link className="button secondary" href="/sync-runs">수집 실행</Link>
          </>
        }
        description="날씨 데이터 수집 현황과 공개 API 상태를 한눈에 확인합니다."
        section="개요"
        title="운영 홈"
      />
      {error ? <div className="error" role="alert">{error}</div> : null}
      <section className="cards home-metrics" aria-busy={loading} aria-label="운영 요약">
        <SummaryCard icon={MapPin} label="활성 위치" value={activeTotal.toLocaleString("ko-KR")} detail="공개 API에 노출된 위치" loading={loading} />
        <SummaryCard icon={Database} label="카탈로그 전체" value={catalogTotal.toLocaleString("ko-KR")} detail="등록된 전체 위치" loading={loading} />
        <SummaryCard icon={Activity} label="최근 수집" value={lastRun ? syncRunStatusLabel(lastRun.status) : "—"} detail={lastRun ? `${lastRun.values_loaded.toLocaleString("ko-KR")}건 저장` : "실행 기록 없음"} loading={loading} />
        <SummaryCard icon={CloudSun} label="API 상태" value={loading ? "—" : health} detail="공개 API 응답 상태" loading={loading} />
      </section>
      <section className="panel dashboard-note">
        <div className="panel-head"><div><h2>운영 기준</h2><p>수집이 실패해도 이전에 저장된 값은 그대로 남습니다.</p></div></div>
        <div className="dashboard-grid">
          <div><span className="eyebrow">위치 카탈로그</span><p>카탈로그에서 켜둔 위치만 수집 대상이 됩니다.</p></div>
          <div><span className="eyebrow">원본 데이터</span><p>각 값이 어떤 원본 응답에서 왔는지 그대로 추적할 수 있습니다.</p></div>
          <div><span className="eyebrow">수정 이력</span><p>값이 수정되면 이전 값도 이력으로 남고, 공개 화면에는 최신 값만 보여줍니다.</p></div>
        </div>
      </section>
      <section className="panel recent-panel">
        <div className="panel-head"><div><h2>최근 실행</h2><p>가장 최근 수집 실행 상태입니다.</p></div></div>
        {lastRun ? <div className="run-summary"><code>{lastRun.run_id}</code><span className={`status ${lastRun.status === "success" ? "on" : "off"}`}>{syncRunStatusLabel(lastRun.status)}</span><span>{lastRun.values_loaded}건 저장 · 격자 {lastRun.grids_fetched}개 · 요청 {lastRun.requests_fetched}건</span></div> : <div className="empty">아직 수집 실행이 없습니다.</div>}
      </section>
    </>
  );
}
