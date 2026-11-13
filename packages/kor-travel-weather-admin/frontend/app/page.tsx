"use client";

import { RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AdminShell } from "@/components/admin-shell";
import { EmptyState } from "@/components/empty-state";
import { SectionCard } from "@/components/section-card";
import { StatStrip, type StatStripItem } from "@/components/stat-strip";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { buttonVariants } from "@/components/ui/button-variants";
import { Card, CardContent } from "@/components/ui/card";
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

const CARD_LABELS = { active: "활성 위치", catalog: "카탈로그 전체", runs: "최근 수집", health: "API 상태" } as const;

/** 네 카드 값은 한 StatStrip을 공유한다. 모바일 1 · 태블릿 2 · 데스크톱 4열로 고정한다. */
const HOME_STAT_COLUMNS = "grid-cols-1 sm:grid-cols-2 xl:grid-cols-4";

const OPERATION_NOTES = [
  { label: "위치 카탈로그", body: "카탈로그에서 켜둔 위치만 수집 대상이 됩니다." },
  { label: "원본 데이터", body: "각 값이 어떤 원본 응답에서 왔는지 그대로 추적할 수 있습니다." },
  { label: "수정 이력", body: "값이 수정되면 이전 값도 이력으로 남고, 공개 화면에는 최신 값만 보여줍니다." },
] as const;

function cardText<T>(state: CardState<T>, format: (value: T) => string): string {
  if (state.status === "ready") return format(state.value);
  return state.status === "error" ? "확인 실패" : "—";
}

function cardDetail<T>(state: CardState<T>, detail: string): string {
  return state.status === "error" ? "불러오지 못함" : detail;
}

/** 카드 하나를 StatStrip 항목으로 바꾼다. 로딩 중인 항목만 `—`로 두고, 끝난 카드는 먼저 그린다. */
function statItem<T>(
  card: keyof typeof CARD_LABELS,
  state: CardState<T>,
  format: (value: T) => string,
  detail: string,
): StatStripItem {
  return {
    key: card,
    label: CARD_LABELS[card],
    value: cardText(state, format),
    caption: state.status === "loading" ? undefined : cardDetail(state, detail),
    loading: state.status === "loading",
  };
}

function runStatItem(state: HomeSummary["runs"]): StatStripItem {
  const last = state.status === "ready" ? state.value[0] : undefined;
  const value = last ? syncRunStatusLabel(last.status) : cardText(state, () => "—");
  const detail = last ? `${last.values_loaded.toLocaleString("ko-KR")}건 저장` : "실행 기록 없음";
  return {
    key: "runs",
    label: CARD_LABELS.runs,
    value,
    caption: state.status === "loading" ? undefined : cardDetail(state, detail),
    loading: state.status === "loading",
  };
}

function summaryItems(summary: HomeSummary): StatStripItem[] {
  return [
    statItem("active", summary.active, (total) => total.toLocaleString("ko-KR"), "공개 API에 노출된 위치"),
    statItem("catalog", summary.catalog, (total) => total.toLocaleString("ko-KR"), "등록된 전체 위치"),
    runStatItem(summary.runs),
    statItem("health", summary.health, (status) => status, "공개 API 응답 상태"),
  ];
}

function RecentRun({ state }: { state: HomeSummary["runs"] }) {
  if (state.status === "loading") return <EmptyState title="불러오는 중…" />;
  if (state.status === "error") return <p className="text-sm text-destructive">실행 기록을 불러오지 못했습니다.</p>;
  const last = state.value[0];
  if (!last) return <EmptyState title="아직 수집 실행이 없습니다." />;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-text-secondary">
      <code className="font-mono text-text-primary">{last.run_id}</code>
      <StatusBadge label={syncRunStatusLabel(last.status)} status={last.status} />
      <span>
        {last.values_loaded}건 저장 · 격자 {last.grids_fetched}개 · 요청 {last.requests_fetched}건
      </span>
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
  const errors = (Object.keys(CARD_LABELS) as (keyof typeof CARD_LABELS)[]).flatMap((card) => {
    const state = summary[card];
    return state.status === "error" ? [`${CARD_LABELS[card]}: ${state.message}`] : [];
  });
  const error = errors.length ? errors.join(" · ") : null;
  return (
    <AdminShell
      actions={
        <>
          {/* 걸린 요청이 있어도 다시 누를 수 있다. 이전 요청의 늦은 응답은 무시된다. */}
          <Button onClick={refresh} type="button" variant="outline">
            <RefreshCwIcon className={loading ? "animate-spin" : undefined} data-icon="inline-start" />
            새로고침
          </Button>
          <Link className={buttonVariants({ variant: "outline" })} href="/weather">
            날씨 지도
          </Link>
          <Link className={buttonVariants({ variant: "outline" })} href="/sync-runs">
            수집 실행
          </Link>
        </>
      }
      description="날씨 데이터 수집 현황과 공개 API 상태를 한눈에 확인합니다."
      section="개요"
      title="운영 홈"
    >
      <div className="space-y-6">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <Card aria-busy={loading || undefined} size="sm">
          <CardContent>
            <StatStrip ariaLabel="운영 요약" className={HOME_STAT_COLUMNS} items={summaryItems(summary)} size="lg" />
          </CardContent>
        </Card>
        <SectionCard description="수집이 실패해도 이전에 저장된 값은 그대로 남습니다." title="운영 기준">
          <div className="grid gap-4 sm:grid-cols-3">
            {OPERATION_NOTES.map((note) => (
              <div className="flex min-w-0 flex-col gap-1" key={note.label}>
                <span className="text-2xs font-medium text-text-secondary">{note.label}</span>
                <p className="text-sm text-text-primary">{note.body}</p>
              </div>
            ))}
          </div>
        </SectionCard>
        <SectionCard description="가장 최근 수집 실행 상태입니다." title="최근 실행">
          <RecentRun state={summary.runs} />
        </SectionCard>
      </div>
    </AdminShell>
  );
}
