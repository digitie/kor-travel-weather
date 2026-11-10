"use client";

import { Activity, CloudSun, Database, MapPin, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import { getHealth, getLocations, getPublicLocations, getSyncRuns, syncRunStatusLabel } from "@/lib/api";
import { type CardState, type HomeFetchers, type HomeSummary, loadingSummary, startHomeSummary } from "@/lib/home-summary";

const HOME_FETCHERS: HomeFetchers = {
  active: async () => {
    const result = await getPublicLocations(1);
    return result.meta.page?.total ?? result.data.length;
  },
  catalog: async () => {
    const result = await getLocations(undefined, 1);
    return result.meta.page?.total ?? result.data.length;
  },
  runs: async () => (await getSyncRuns(10)).data,
  health: async () => {
    const result = await getHealth();
    return result.status === "ok" ? "정상" : result.status;
  },
};

function cardText<T>(state: CardState<T>, format: (value: T) => string): string {
  if (state.status === "ready") return format(state.value);
  return "—";
}

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
  const [summary, setSummary] = useState<HomeSummary>(loadingSummary);
  const cancelPrevious = useRef<(() => void) | null>(null);

  // 카드마다 끝나는 대로 그린다. 느린 요청·실패 하나가 다른 카드를 붙잡거나 지우지 않는다.
  const refresh = useCallback(() => {
    cancelPrevious.current?.();
    setSummary(loadingSummary());
    cancelPrevious.current = startHomeSummary(HOME_FETCHERS, (card, state) => {
      setSummary((previous) => ({ ...previous, [card]: state }));
    });
  }, []);

  useEffect(() => {
    refresh();
    return () => cancelPrevious.current?.();
  }, [refresh]);

  const loading = Object.values(summary).some((card) => card.status === "loading");
  const errors = Object.values(summary).flatMap((card) => (card.status === "error" ? [card.message] : []));
  const error = errors.length ? [...new Set(errors)].join(" · ") : null;
  const runs = summary.runs.status === "ready" ? summary.runs.value : [];
  const lastRun = runs[0];
  return (
    <>
      <PageHeader
        actions={
          <>
            {/* 걸린 요청이 있어도 다시 누를 수 있다. 이전 요청의 늦은 응답은 무시된다. */}
            <button className="button secondary" onClick={refresh} type="button">
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
        <SummaryCard icon={MapPin} label="활성 위치" value={cardText(summary.active, (total) => total.toLocaleString("ko-KR"))} detail="공개 API에 노출된 위치" loading={summary.active.status === "loading"} />
        <SummaryCard icon={Database} label="카탈로그 전체" value={cardText(summary.catalog, (total) => total.toLocaleString("ko-KR"))} detail="등록된 전체 위치" loading={summary.catalog.status === "loading"} />
        <SummaryCard icon={Activity} label="최근 수집" value={summary.runs.status === "ready" ? (lastRun ? syncRunStatusLabel(lastRun.status) : "—") : "—"} detail={summary.runs.status === "error" ? "불러오지 못함" : lastRun ? `${lastRun.values_loaded.toLocaleString("ko-KR")}건 저장` : "실행 기록 없음"} loading={summary.runs.status === "loading"} />
        <SummaryCard icon={CloudSun} label="API 상태" value={cardText(summary.health, (status) => status)} detail="공개 API 응답 상태" loading={summary.health.status === "loading"} />
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
