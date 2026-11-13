"use client";

import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { type ColumnDef } from "@kor-travel/ui/virtual-table";

import { AdminShell } from "@/components/admin-shell";
import { DetailList, type DetailItem } from "@/components/detail-list";
import { EmptyState } from "@/components/empty-state";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DataTable, type DataTableColumnMeta } from "@/components/ui/data-table";
import {
  REGIONAL_ALERT_DATASET,
  REGIONAL_ALERT_LABEL,
  syncRunErrorText,
} from "@/lib/alert-event-labels";
import {
  getProviders,
  getSyncRunSources,
  getSyncRuns,
  syncRunStatusLabel,
  type Provider,
  type SourceRecordSummary,
  type SyncRun,
} from "@/lib/api";

function timestamp(value: string | null) {
  return value
    ? new Date(value).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })
    : "—";
}

function providerLabel(providers: Map<string, Provider>, providerKey: string) {
  return providers.get(providerKey)?.label ?? providerKey;
}

function datasetLabel(
  providers: Map<string, Provider>,
  providerKey: string,
  datasetKey: string,
) {
  if (datasetKey === REGIONAL_ALERT_DATASET) return REGIONAL_ALERT_LABEL;
  return (
    providers
      .get(providerKey)
      ?.datasets.find((dataset) => dataset.key === datasetKey)?.label ??
    datasetKey
  );
}

function RunDetail({
  run,
  providers,
}: {
  run: SyncRun;
  providers: Map<string, Provider>;
}) {
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
      setSourcesError(
        reason instanceof Error
          ? reason.message
          : "원본 데이터를 불러오지 못했습니다.",
      );
      setSources([]);
    }
  }

  const details: DetailItem[] = [
    { label: "실행 ID", value: run.run_id, mono: true },
    { label: "마지막 응답", value: timestamp(run.heartbeat_at), mono: true },
    { label: "종료 시각", value: timestamp(run.finished_at), mono: true },
    { label: "대상 위치", value: run.locations_total, mono: true },
    { label: "격자 수", value: run.grids_fetched, mono: true },
    { label: "중기예보 그룹 수", value: run.mid_groups_fetched, mono: true },
    { label: "요청 수", value: run.requests_fetched, mono: true },
    { label: "저장된 값 수", value: run.values_loaded, mono: true },
  ];

  function renderSources() {
    if (!showSources) return null;
    if (sourcesError) {
      return (
        <Alert variant="destructive">
          <AlertDescription>{sourcesError}</AlertDescription>
        </Alert>
      );
    }
    if (sources === null) {
      return (
        <p aria-busy="true" className="text-xs text-text-secondary">
          불러오는 중…
        </p>
      );
    }
    if (sources.length === 0) {
      return <EmptyState size="sm" title="연결된 원본 데이터가 없습니다." />;
    }
    return (
      <DataTable
        ariaLabel="실행 원본 데이터"
        data={sources}
        getRowId={(source) => source.source_record_key}
        columns={
          [
            {
              id: "source0",
              header: "원본 ID",
              enableSorting: false,
              cell: ({ row }) => (
                <code className="font-mono text-xs">
                  {row.original.source_record_key}
                </code>
              ),
            },
            {
              id: "source1",
              header: "데이터셋",
              enableSorting: false,
              cell: ({ row }) => (
                <span title={row.original.dataset_key}>
                  {datasetLabel(
                    providers,
                    row.original.provider,
                    row.original.dataset_key,
                  )}
                </span>
              ),
            },
            {
              id: "source2",
              header: "대상",
              enableSorting: false,
              cell: ({ row }) => (
                <code className="font-mono text-xs">
                  {row.original.source_entity_id}
                </code>
              ),
            },
            {
              id: "source3",
              header: "행 수",
              enableSorting: false,
              meta: { align: "right" } satisfies DataTableColumnMeta,
              cell: ({ row }) => <span>{row.original.row_count ?? "—"}</span>,
            },
            {
              id: "source4",
              header: "가져온 시각",
              enableSorting: false,
              cell: ({ row }) => (
                <span className="font-mono text-xs text-text-secondary">
                  {timestamp(row.original.fetched_at)}
                </span>
              ),
            },
          ] satisfies ColumnDef<SourceRecordSummary, unknown>[]
        }
      />
    );
  }

  return (
    <section
      aria-label={run.run_id}
      className="flex flex-col gap-4 bg-surface-subtle px-4 py-4"
    >
      {run.error ? (
        <div className="flex flex-col gap-1">
          <span className="text-2xs font-medium text-text-secondary">
            실패 원인
          </span>
          <p className="text-sm text-destructive">
            {syncRunErrorText(run.dataset_key, run.error)}
          </p>
        </div>
      ) : null}
      <DetailList items={details} />
      <div className="flex flex-col gap-3">
        <Button
          className="self-start"
          onClick={() => void loadSources()}
          size="sm"
          type="button"
          variant="ghost"
        >
          {showSources ? (
            <ChevronDownIcon data-icon="inline-start" />
          ) : (
            <ChevronRightIcon data-icon="inline-start" />
          )}
          원본 데이터 {sources ? `(${sources.length}건)` : ""}
        </Button>
        {renderSources()}
      </div>
    </section>
  );
}

export default function SyncRunsPage() {
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [providers, setProviders] = useState<Map<string, Provider>>(
    () => new Map(),
  );
  const [error, setError] = useState<string | null>(null);
  const [store] = useState(() =>
    createStore<{ expandedRun: string | null }>(() => ({ expandedRun: null })),
  );
  const expandedRun = useStore(store, (state) => state.expandedRun);
  const setExpandedRun = (expandedRun: string | null) =>
    store.setState({ expandedRun });

  useEffect(() => {
    getSyncRuns()
      .then((result) => setRuns(result.data))
      .catch((reason: unknown) =>
        setError(
          reason instanceof Error
            ? reason.message
            : "실행 이력을 불러오지 못했습니다.",
        ),
      );
    getProviders()
      .then((result) =>
        setProviders(
          new Map(result.data.map((provider) => [provider.provider, provider])),
        ),
      )
      .catch(() => undefined);
  }, []);

  return (
    <AdminShell
      description="예약 일정에 따라 날씨 데이터를 가져온 결과입니다. 실패한 항목은 표에서 바로 보이고, 행을 펼치면 실패 원인과 어떤 원본 데이터를 가져왔는지 확인할 수 있습니다."
      section="수집 파이프라인"
      title="수집 실행"
    >
      <Card size="sm">
        {error ? (
          <CardContent>
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          </CardContent>
        ) : null}
        {runs.length === 0 ? (
          <CardContent>
            <EmptyState title="아직 수집 실행이 없습니다." />
          </CardContent>
        ) : (
          <DataTable
            ariaLabel="수집 실행 목록"
            data={runs}
            renderRowDetails={(run) =>
              run.run_id === expandedRun ? (
                <RunDetail run={run} providers={providers} />
              ) : null
            }
            getRowId={(run) => run.run_id}
            isRowActive={(run) => run.run_id === expandedRun}
            columns={
              [
                {
                  id: "run0",
                  header: "실행",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const run = row.original;
                    const expanded = expandedRun === run.run_id;
                    return (
                      <Button
                        aria-expanded={expanded}
                        onClick={() =>
                          setExpandedRun(expanded ? null : run.run_id)
                        }
                        size="sm"
                        type="button"
                        variant="ghost"
                      >
                        {expanded ? (
                          <ChevronDownIcon data-icon="inline-start" />
                        ) : (
                          <ChevronRightIcon data-icon="inline-start" />
                        )}
                        <code className="font-mono text-xs">
                          {run.run_id.slice(0, 8)}…
                        </code>
                      </Button>
                    );
                  },
                },
                {
                  id: "run1",
                  header: "제공처",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const run = row.original;
                    return (
                      <span className="font-medium text-text-primary" title={run.provider}>
                        {providerLabel(providers, run.provider)}
                      </span>
                    );
                  },
                },
                {
                  id: "run2",
                  header: "데이터셋",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const run = row.original;
                    return (
                      <span title={run.dataset_key}>
                        {datasetLabel(providers, run.provider, run.dataset_key)}
                      </span>
                    );
                  },
                },
                {
                  id: "run3",
                  header: "상태",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const run = row.original;
                    return (
                      <StatusBadge
                        label={syncRunStatusLabel(run.status)}
                        status={run.status}
                      />
                    );
                  },
                },
                {
                  id: "run4",
                  header: "시작",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const run = row.original;
                    return (
                      <span className="font-mono text-xs text-text-secondary">
                        {timestamp(run.started_at)}
                      </span>
                    );
                  },
                },
                {
                  id: "run5",
                  header: "값 수",
                  enableSorting: false,
                  meta: { align: "right" } satisfies DataTableColumnMeta,
                  cell: ({ row }) => <span>{row.original.values_loaded}</span>,
                },
                {
                  id: "run6",
                  header: "오류",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const run = row.original;
                    const errorText = syncRunErrorText(
                      run.dataset_key,
                      run.error,
                    );
                    return (
                      <span
                        className={errorText ? "text-destructive" : undefined}
                        title={errorText ?? undefined}
                      >
                        {errorText
                          ? errorText.length > 60
                            ? errorText.slice(0, 60) + "…"
                            : errorText
                          : "—"}
                      </span>
                    );
                  },
                },
              ] satisfies ColumnDef<SyncRun, unknown>[]
            }
          />
        )}
      </Card>
    </AdminShell>
  );
}
