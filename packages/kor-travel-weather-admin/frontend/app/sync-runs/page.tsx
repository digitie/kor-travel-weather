"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { VirtualTable, type ColumnDef } from "@kor-travel/ui/virtual-table";
import { Button } from "@kor-travel/ui/primitives";

import { PageHeader } from "@/components/admin-shell";
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
  Provider,
  SourceRecordSummary,
  SyncRun,
} from "@/lib/api";

function statusClass(status: string) {
  if (status === "success") return "on";
  if (status === "running") return "warn";
  return "off";
}

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

  return (
    <section className="sync-run-detail-row" aria-label={run.run_id}>
      <div className="sync-run-detail">
        {run.error ? (
          <div>
            <div className="eyebrow">실패 원인</div>
            <p className="error-cell">
              {syncRunErrorText(run.dataset_key, run.error)}
            </p>
          </div>
        ) : null}
        <div className="sync-run-detail-grid">
          <div>
            <span>실행 ID</span>
            <code>{run.run_id}</code>
          </div>
          <div>
            <span>마지막 응답</span>
            <code>{timestamp(run.heartbeat_at)}</code>
          </div>
          <div>
            <span>종료 시각</span>
            <code>{timestamp(run.finished_at)}</code>
          </div>
          <div>
            <span>대상 위치</span>
            <code>{run.locations_total}</code>
          </div>
          <div>
            <span>격자 수</span>
            <code>{run.grids_fetched}</code>
          </div>
          <div>
            <span>중기예보 그룹 수</span>
            <code>{run.mid_groups_fetched}</code>
          </div>
          <div>
            <span>요청 수</span>
            <code>{run.requests_fetched}</code>
          </div>
          <div>
            <span>저장된 값 수</span>
            <code>{run.values_loaded}</code>
          </div>
        </div>
        <div>
          <Button
            className="ghost"
            type="button"
            onClick={() => void loadSources()}
          >
            {showSources ? (
              <ChevronDown size={14} />
            ) : (
              <ChevronRight size={14} />
            )}
            원본 데이터 {sources ? `(${sources.length}건)` : ""}
          </Button>
          {showSources ? (
            sourcesError ? (
              <div className="error" role="alert">
                {sourcesError}
              </div>
            ) : sources === null ? (
              <div className="loading" aria-busy="true">
                불러오는 중…
              </div>
            ) : sources.length === 0 ? (
              <div className="empty">연결된 원본 데이터가 없습니다.</div>
            ) : (
              <div className="table-wrap">
                <VirtualTable
                  ariaLabel="실행 원본 데이터"
                  data={sources}
                  getRowId={(source) => source.source_record_key}
                  columns={
                    [
                      {
                        id: "source0",
                        header: "원본 ID",
                        enableSorting: false,
                        cell: ({ row }) => {
                          const source = row.original;
                          return (
                            <span>
                              {" "}
                              <code>{source.source_record_key}</code>{" "}
                            </span>
                          );
                        },
                      },
                      {
                        id: "source1",
                        header: "데이터셋",
                        enableSorting: false,
                        cell: ({ row }) => {
                          const source = row.original;
                          return (
                            <span title={source.dataset_key}>
                              {" "}
                              {datasetLabel(
                                providers,
                                source.provider,
                                source.dataset_key,
                              )}{" "}
                            </span>
                          );
                        },
                      },
                      {
                        id: "source2",
                        header: "대상",
                        enableSorting: false,
                        cell: ({ row }) => {
                          const source = row.original;
                          return (
                            <span>
                              {" "}
                              <code>{source.source_entity_id}</code>{" "}
                            </span>
                          );
                        },
                      },
                      {
                        id: "source3",
                        header: "행 수",
                        enableSorting: false,
                        cell: ({ row }) => {
                          const source = row.original;
                          return <span> {source.row_count ?? "—"} </span>;
                        },
                      },
                      {
                        id: "source4",
                        header: "가져온 시각",
                        enableSorting: false,
                        cell: ({ row }) => {
                          const source = row.original;
                          return (
                            <span>
                              {" "}
                              <code>{timestamp(source.fetched_at)}</code>{" "}
                            </span>
                          );
                        },
                      },
                    ] satisfies ColumnDef<SourceRecordSummary, unknown>[]
                  }
                />
              </div>
            )
          ) : null}
        </div>
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
    <>
      <PageHeader
        description="예약 일정에 따라 날씨 데이터를 가져온 결과입니다. 실패한 항목은 표에서 바로 보이고, 행을 펼치면 실패 원인과 어떤 원본 데이터를 가져왔는지 확인할 수 있습니다."
        section="수집 파이프라인"
        title="수집 실행"
      />
      <section className="panel">
        {error ? (
          <div className="error" role="alert">
            {error}
          </div>
        ) : null}
        {runs.length === 0 ? (
          <div className="empty">아직 수집 실행이 없습니다.</div>
        ) : (
          <div className="table-wrap">
            <VirtualTable
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
                        <span>
                          <Button
                            className="ghost row-expand-toggle"
                            type="button"
                            onClick={() =>
                              setExpandedRun(expanded ? null : run.run_id)
                            }
                            aria-expanded={expanded}
                          >
                            {expanded ? (
                              <ChevronDown size={14} />
                            ) : (
                              <ChevronRight size={14} />
                            )}
                            <code>{run.run_id.slice(0, 8)}…</code>
                          </Button>
                        </span>
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
                        <span>
                          {" "}
                          <strong title={run.provider}>
                            {providerLabel(providers, run.provider)}
                          </strong>{" "}
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
                          {" "}
                          {datasetLabel(
                            providers,
                            run.provider,
                            run.dataset_key,
                          )}{" "}
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
                        <span>
                          {" "}
                          <span className={`status ${statusClass(run.status)}`}>
                            {syncRunStatusLabel(run.status)}
                          </span>{" "}
                        </span>
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
                        <span>
                          {" "}
                          <code>{timestamp(run.started_at)}</code>{" "}
                        </span>
                      );
                    },
                  },
                  {
                    id: "run5",
                    header: "값 수",
                    enableSorting: false,
                    cell: ({ row }) => {
                      const run = row.original;
                      return <span> {run.values_loaded} </span>;
                    },
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
                          className="error-cell"
                          title={errorText ?? undefined}
                        >
                          {" "}
                          {errorText
                            ? errorText.length > 60
                              ? errorText.slice(0, 60) + "…"
                              : errorText
                            : "—"}{" "}
                        </span>
                      );
                    },
                  },
                ] satisfies ColumnDef<SyncRun, unknown>[]
              }
            />
          </div>
        )}
      </section>
    </>
  );
}
