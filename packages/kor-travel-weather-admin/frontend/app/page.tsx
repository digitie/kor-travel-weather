"use client";

import { Activity, CloudSun, Database, MapPin, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import { getHealth, getLocations, getPublicLocations, getSyncRuns, SyncRun } from "@/lib/api";

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
        description="KMA 원천 응답과 공개용 weather fact를 한 곳에서 점검합니다."
        section="Overview"
        title="Weather source"
      />
      {error ? <div className="error" role="alert">{error}</div> : null}
      <section className="cards home-metrics" aria-busy={loading} aria-label="운영 요약">
        <SummaryCard icon={MapPin} label="활성 위치" value={activeTotal.toLocaleString("ko-KR")} detail="public catalog" loading={loading} />
        <SummaryCard icon={Database} label="카탈로그 전체" value={catalogTotal.toLocaleString("ko-KR")} detail="관리 대상 anchor" loading={loading} />
        <SummaryCard icon={Activity} label="최근 수집" value={lastRun?.status ?? "—"} detail={lastRun ? `${lastRun.values_loaded.toLocaleString("ko-KR")} facts` : "실행 기록 없음"} loading={loading} />
        <SummaryCard icon={CloudSun} label="API 상태" value={health} detail="weather source" loading={loading} />
      </section>
      <section className="panel dashboard-note">
        <div className="panel-head"><div><h2>운영 기준</h2><p>수집 실패 시 이전 immutable fact는 그대로 보존됩니다.</p></div></div>
        <div className="dashboard-grid">
          <div><span className="eyebrow">catalog</span><p>위치 카탈로그의 enabled 상태가 Dagster 실행 대상의 기준입니다.</p></div>
          <div><span className="eyebrow">lineage</span><p>각 metric은 원천 response의 source_record_key를 공유합니다.</p></div>
          <div><span className="eyebrow">revision</span><p>수정 응답은 history로 남고 public latest는 최신 revision만 선택합니다.</p></div>
        </div>
      </section>
      <section className="panel recent-panel">
        <div className="panel-head"><div><h2>최근 실행</h2><p>Dagster run의 마지막 상태</p></div></div>
        {lastRun ? <div className="run-summary"><code>{lastRun.run_id}</code><span className={`status ${lastRun.status === "success" ? "on" : "off"}`}>{lastRun.status}</span><span>{lastRun.values_loaded} facts · {lastRun.grids_fetched} grids · {lastRun.requests_fetched} requests</span></div> : <div className="empty">아직 수집 실행이 없습니다.</div>}
      </section>
    </>
  );
}
