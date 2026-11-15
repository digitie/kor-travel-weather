/** 관리자 콘솔은 실행 성공과 자료 수집 상태를 서로 구분한다. */
export type ConsoleCapabilities = { launch: boolean; cancel: boolean; retry: boolean; schedule: boolean; sensor: boolean };
export type ConsoleInstigator = { name: string; status: string; canStart: boolean; canStop: boolean; jobName?: string; cron?: string };
export type ConsoleCatalog = { jobs: string[]; schedules: ConsoleInstigator[]; sensors: ConsoleInstigator[]; capabilities: ConsoleCapabilities; checkedAt: string };
export type ConsoleStep = { key: string; status: string; startTime: number | null; endTime: number | null; attempts: number };
export type ConsoleEvent = { kind: string; time: number | null; step: string | null; title: string; detail: string | null; warning: boolean; counts: Record<string, number> };
export type ConsoleRunDetail = {
  runId: string; jobName: string; status: string; creationTime: number | null;
  startTime: number | null; endTime: number | null; queuedTime: number | null;
  canCancel: boolean; canRetry: boolean; steps: ConsoleStep[]; events: ConsoleEvent[];
  stepsSucceeded: number | null; stepsFailed: number | null; materializations: number | null;
  cursor: string | null; hasMore: boolean; stepsTruncated: boolean; checkedAt: string;
};
export type ConsoleAction = "launch" | "retry" | "cancel" | "startSchedule" | "stopSchedule" | "startSensor" | "stopSensor";
export type ConsoleActionRequest = { operation: "action"; action: ConsoleAction; requestId: string; name?: string; runId?: string };
export type ConsoleRequest = { operation: "catalog" } | { operation: "detail"; runId: string; cursor?: string | null; includeEvents?: boolean } | { operation: "requestStatus"; requestId: string } | ConsoleActionRequest;
export type ConsoleActionResult = { state: "accepted" | "unchanged" | "blocked" | "unknown" | "rejected"; message: string; runId?: string; replayed?: boolean; requestId?: string };

export const CONSOLE_COUNT_LABELS: Record<string, string> = {
  planned_grids: "계획 격자", grid_failure_PROVIDER_NETWORK: "제공기관 연결 실패", grid_failure_PROVIDER_SERVER: "제공기관 서버 오류", grid_failure_PROVIDER_PARSE: "제공기관 응답 해석 실패", grid_failure_PROVIDER_NO_DATA: "제공기관 자료 없음", grid_failure_DB_LOCK_CONTENTION: "DB 잠금 경합",
  fetch_ms: "요청 시간(ms)", normalize_ms: "정규화 시간(ms)", publish_ms: "게시 시간(ms)",
  values_attempted: "시도 값", values_skipped: "건너뛴 값", missing_values: "결측 값", invalid_values: "범위 밖 값",
  published_values: "저장 값", values_loaded: "저장 값", completed_grids: "완료 격자", grids_fetched: "조회 격자", requests_fetched: "조회 요청",
  raw_response_count: "저장 원문", fee_rule_count: "요금 규칙", collection_run_id: "수집 실행 ID", traffic_snapshot_count: "소통 저장", incident_snapshot_count: "돌발 저장",
  fuel_station_count: "주유소 저장", fuel_price_count: "가격 저장", snapshot_count: "관측 저장", error_count: "오류",
  received_rows: "수신 행", parsed_rows: "해석 행", invalid_rows: "불량 행", failed_dataset_count: "실패 원천", successful_dataset_count: "성공 원천",
  failed_grids: "미완료 격자", deferred_grids: "재개 대기 격자", failed_requests: "실패 요청", completed_sources: "완료 원천", failed_sources: "미완료 원천",
};
export const CONSOLE_ACTIVE = new Set(["NOT_STARTED", "QUEUED", "STARTING", "STARTED", "CANCELING"]);
export const CONSOLE_STATUS_LABELS: Record<string, string> = {
  NOT_STARTED: "시작 대기", QUEUED: "실행 대기", STARTING: "시작 중", STARTED: "실행 중", IN_PROGRESS: "실행 중", CANCELING: "취소 중",
  SUCCESS: "실행 완료", FAILURE: "실행 실패", CANCELED: "취소됨", SKIPPED: "건너뜀", PREPARING: "준비 중", RETRY_REQUESTED: "재시도 대기",
};
export function consoleStatus(value: string) { return CONSOLE_STATUS_LABELS[value] ?? "확인 필요"; }
