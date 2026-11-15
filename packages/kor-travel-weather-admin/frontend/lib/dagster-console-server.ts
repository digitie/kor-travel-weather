import { createHash } from "node:crypto";
import { DAGSTER_LOCATION_NAME, DAGSTER_REPOSITORY_NAME, DAGSTER_REPOSITORY_TAG_KEY, DAGSTER_REPOSITORY_TAG_VALUE,
  CONSOLE_CATALOG_QUERY, CONSOLE_RUN_QUERY, BASIC_RUN_QUERY, CONSOLE_EVENTS_QUERY, FIND_REQUEST, ACTIVE_JOB, LAUNCH, CANCEL, START_SCHEDULE, STOP_SCHEDULE, START_SENSOR, STOP_SENSOR
} from "./dagster-scope";
export { CONSOLE_CATALOG_QUERY, CONSOLE_RUN_QUERY, CONSOLE_EVENTS_QUERY } from "./dagster-scope";
import { failureDiagnostic, gridFailureCounts } from "./dagster-failure-diagnostics";
import { CONSOLE_ACTIVE, CONSOLE_COUNT_LABELS, type ConsoleActionRequest, type ConsoleActionResult, type ConsoleCatalog, type ConsoleEvent, type ConsoleRequest, type ConsoleRunDetail } from "./dagster-console-model";

type Row = Record<string, unknown>;
type Graphql = (query: string, variables: Record<string, unknown>) => Promise<Row>;
const row = (value: unknown): Row => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const string = (value: unknown) => typeof value === "string" ? value : "";
const identifier = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9_][A-Za-z0-9_.\[\]-]{0,199}$/.test(value) ? value : null;
const uuid = (value: unknown) => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const number = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1e12 ? value : null;
// Run/단계는 초, native MessageEvent.timestamp는 밀리초다. 건수 상한과 섞지 않는다.
const clock = (value: unknown, unit: "seconds" | "milliseconds" = "seconds"): number | null => {
  const numeric = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
  if(typeof numeric !== "number" || !Number.isFinite(numeric) || numeric < 0)return null;
  const seconds = unit === "milliseconds" ? numeric / 1000 : numeric;
  return seconds <= 8_640_000_000_000 ? seconds : null;
};
const status = (value: unknown) => ["NOT_STARTED", "QUEUED", "STARTING", "STARTED", "CANCELING", "SUCCESS", "FAILURE", "CANCELED", "SKIPPED", "IN_PROGRESS", "RETRY_REQUESTED"].includes(string(value)) ? string(value) : "UNKNOWN";
const tag = { key: DAGSTER_REPOSITORY_TAG_KEY, value: DAGSTER_REPOSITORY_TAG_VALUE };
const selector = { repositoryLocationName: DAGSTER_LOCATION_NAME, repositoryName: DAGSTER_REPOSITORY_NAME };

// 실제 native union에서 실행 생성 전의 검증·권한 거절로 확정되는 종류만 분류한다.
const launchRejections:Record<string,string> = {
  UnauthorizedError:"이 작업을 실행할 권한이 없습니다.",
  RunConfigValidationInvalid:"기본 실행 설정이 거절됐습니다. 작업 설정을 확인하세요.",
  NoModeProvidedError:"기본 실행 모드를 확인해야 합니다. 작업 설정을 확인하세요.",
  ConflictingExecutionParamsError:"실행 설정이 서로 맞지 않습니다. 작업 설정을 확인하세요.",
  PresetNotFoundError:"현재 작업의 실행 설정을 찾지 못했습니다. 제어 상태를 새로 확인하세요.",
  PipelineNotFoundError:"현재 작업 정의를 찾지 못했습니다. 제어 상태를 새로 확인하세요.",
  InvalidStepError:"실행 단계가 현재 작업 정의와 맞지 않습니다. 제어 상태를 새로 확인하세요.",
  InvalidOutputError:"실행 출력이 현재 작업 정의와 맞지 않습니다. 제어 상태를 새로 확인하세요.",
  InvalidSubsetError:"실행 대상이 현재 작업 정의와 맞지 않습니다. 제어 상태를 새로 확인하세요.",
};

export class ConsoleServiceError extends Error { constructor(readonly status: number) { super("Dagster console request failed"); } }

/** 고정 위치와 UUID를 결합해 서로 다른 앱의 같은 요청 ID가 전역 저장소에서 충돌하지 않게 한다. */
export function consoleRequestId(id: string) {
  const bytes = createHash("sha256").update(`${DAGSTER_LOCATION_NAME}\n${id.toLowerCase()}`).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 0x0f) | 0x40; bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

export function parseConsoleRequest(raw: unknown): ConsoleRequest | null {
  const value = row(raw), operation = value.operation;
  if (operation === "catalog" && Object.keys(value).length === 1) return { operation };
  if (operation === "requestStatus" && Object.keys(value).length === 2 && uuid(value.requestId)) return { operation, requestId: string(value.requestId).toLowerCase() };
  if (operation === "detail" && Object.keys(value).every(key => ["operation", "runId", "cursor", "includeEvents"].includes(key)) && uuid(value.runId)
    && (value.includeEvents === undefined || typeof value.includeEvents === "boolean")
    && (value.cursor == null || typeof value.cursor === "string" && value.cursor.length <= 512)) return { operation, runId: string(value.runId).toLowerCase(), cursor: typeof value.cursor === "string" ? value.cursor : null, ...(value.includeEvents === false ? { includeEvents: false } : {}) };
  if (operation !== "action" || !uuid(value.requestId) || Object.keys(value).some(key => !["operation", "action", "requestId", "name", "runId"].includes(key))) return null;
  const action = value.action;
  if (["launch", "startSchedule", "stopSchedule", "startSensor", "stopSensor"].includes(string(action)) && identifier(value.name) && value.runId === undefined) return { operation, action, name: value.name, requestId: string(value.requestId).toLowerCase() } as ConsoleActionRequest;
  if (["retry", "cancel"].includes(string(action)) && uuid(value.runId) && value.name === undefined) return { operation, action, runId: string(value.runId).toLowerCase(), requestId: string(value.requestId).toLowerCase() } as ConsoleActionRequest;
  return null;
}

function runs(data: Row): Row[] {
  const result = row(data.runsOrError);
  if (result.__typename !== "Runs" || !Array.isArray(result.results)) throw new ConsoleServiceError(502);
  return result.results.map(row);
}
const filter = (extra: Row = {}) => ({ ...extra, tags: [tag, ...list(extra.tags)] });
function runFilter(runId: string) { return filter({ runIds: [runId] }); }
function requiredRun(data: Row, runId: string) {
  const value = runs(data)[0];
  if (!value || value.runId !== runId || !identifier(value.jobName)) throw new ConsoleServiceError(404);
  return value;
}
function effectiveState(value: Row, state: Row) { return string(state.status) === "DECLARED_IN_CODE" ? string(value.defaultStatus) : string(state.status); }
const outcomes: Record<string,string> = { success: "자료 수집 완료", partial: "일부 자료 미완료 · 정상 자료 유지", partial_success: "일부 자료 미완료 · 정상 자료 유지", deferred: "다음 수집으로 이월 · 정상 자료 유지", failed: "자료 수집 실패 · 마지막 정상 자료 유지", skipped: "이번 수집 건너뜀", not_ready: "원천 설정 또는 수집 주기로 대기", no_data: "원천 자료 미제공", collected: "자료 수집 완료", collected_with_warnings: "자료 저장 완료 · 후속 처리 경고", cache_refresh_failed: "자료 저장 완료 · 읽기 캐시 갱신 지연", source_unavailable: "원천 응답 지연 · 정상 자료 유지" };
function safeMetadata(entries: unknown) {
  const counts: Record<string,number> = {}, notes: string[] = []; let warning = false, diagnostic = false;
  for (const raw of list(entries).slice(0,64)) {
    const item = row(raw), key = string(item.label), value = item.intValue ?? item.floatValue;
    if (Object.hasOwn(CONSOLE_COUNT_LABELS, key) && number(value) !== null) counts[key] = value as number;
    if ((key === "status" || key === "collection_outcome") && Object.hasOwn(outcomes, string(item.text))) {
      notes.push(outcomes[string(item.text)]); warning ||= !["success", "collected"].includes(string(item.text));
    }
    if (key === "cache_refresh_failed" && item.boolValue === true) { notes.push("읽기 캐시 갱신 지연 · 저장 자료는 유지"); warning = true; }
    if (["successful_data_preserved", "previous_data_preserved"].includes(key) && item.boolValue === true) notes.push("정상 저장분 보존");
    if (key === "deferred_reason" && item.text === "collector_busy") notes.push("같은 수집이 이미 진행 중입니다.");
    if (key === "deferred_reason" && item.text === "cooldown_or_disabled") notes.push("수집 주기 또는 원천 활성 상태에 따라 대기합니다.");
    if (key === "skipped" && item.boolValue === true) { notes.push("이번 수집 건너뜀 · 신규 적재 완료를 뜻하지 않음"); warning = true; }
    if (key === "error_code" && typeof item.text === "string") diagnostic = true;
  }
  for(const [code,count] of Object.entries(gridFailureCounts(entries))){counts[`grid_failure_${code}`]=count;warning ||= count>0;}
  return { counts, notes: [...new Set(notes)], warning, diagnostic };
}
const eventTitles: Record<string,string> = { RunStartEvent: "실행 시작", RunStartingEvent: "실행 준비", RunEnqueuedEvent: "대기열 등록", RunDequeuedEvent: "대기열에서 선택", RunSuccessEvent: "실행 완료", RunCanceledEvent: "실행 취소", RunCancelingEvent: "취소 처리", ExecutionStepStartEvent: "단계 시작", ExecutionStepSuccessEvent: "단계 완료", ExecutionStepSkippedEvent: "단계 건너뜀", ExecutionStepRestartEvent: "단계 재시작", ExecutionStepUpForRetryEvent: "단계 재시도 대기", ExecutionStepOutputEvent: "처리 결과", HandledOutputEvent: "결과 저장", MaterializationEvent: "자료 게시", EngineEvent: "실행기 상태" };
export function sanitizeConsoleEvents(value: unknown): ConsoleEvent[] {
  const result: ConsoleEvent[] = [];
  for (const raw of list(value).slice(0,200)) {
    const event = row(raw), kind = string(event.__typename), metadata = safeMetadata(event.metadataEntries);
    const failure = kind === "ExecutionStepFailureEvent" || kind === "RunFailureEvent";
    let title = eventTitles[kind], detail: string | null = null;
    if (failure) { title = kind === "RunFailureEvent" ? "실행 실패" : "단계 실패"; detail = failureDiagnostic(event); }
    else if (kind === "LogMessageEvent") {
      if (!/^kma_collection_progress\b/.test(string(event.message))) continue;
      title = "KMA 처리 진행";
      for (const key of ["fetch_ms", "normalize_ms", "publish_ms", "published_values", "completed_grids", "requests_fetched"]) {
        const match = string(event.message).slice(0,4096).match(new RegExp(`['\"]?${key}['\"]?\\s*:\\s*([0-9]{1,12})\\b`));
        if (match) metadata.counts[key] = Number(match[1]);
      }
    } else if (!title) continue;
    if (!failure && metadata.diagnostic) detail = failureDiagnostic({ failureMetadata: { metadataEntries: event.metadataEntries } });
    const notes = metadata.notes.join("\n");
    if (notes) detail = detail ? `${notes}\n${detail}` : notes;
    result.push({ kind, time: clock(event.timestamp,"milliseconds"), step: identifier(event.stepKey), title, detail, warning: failure || metadata.warning || metadata.diagnostic, counts: metadata.counts });
  }
  return result;
}

export function sanitizeConsoleRun(value: Row, eventData?: Row): ConsoleRunDetail {
  const stats = row(value.stats), stepStats = list(value.stepStats).map(row), plan = list(row(value.executionPlan).steps).map(row);
  const keys = [...new Set([...plan.map(item => identifier(item.key)), ...stepStats.map(item => identifier(item.stepKey))].filter((key): key is string => key !== null))];
  const connection = row(eventData?.eventConnection);
  return { runId: string(value.runId), jobName: string(value.jobName), status: status(value.status), creationTime: clock(value.creationTime), startTime: clock(value.startTime), endTime: clock(value.endTime), queuedTime: clock(stats.enqueuedTime),
    canCancel: value.canTerminate === true && value.hasTerminatePermission === true && CONSOLE_ACTIVE.has(status(value.status)) && value.status !== "CANCELING",
    canRetry: value.hasReExecutePermission === true && ["FAILURE", "CANCELED"].includes(status(value.status)),
    steps: keys.slice(0,1000).map(key => { const item = stepStats.find(entry => entry.stepKey === key) ?? {}; return { key, status: item.status == null ? "NOT_STARTED" : status(item.status), startTime: clock(item.startTime), endTime: clock(item.endTime), attempts: list(item.attempts).length }; }),
    events: sanitizeConsoleEvents(connection.events), stepsSucceeded: number(stats.stepsSucceeded), stepsFailed: number(stats.stepsFailed), materializations: number(stats.materializations),
    cursor: typeof connection.cursor === "string" && connection.cursor.length <= 512 ? connection.cursor : null, hasMore: connection.hasMore === true, stepsTruncated: keys.length > 1000, checkedAt: new Date().toISOString(),
  };
}

export async function handleConsoleRequest(input: ConsoleRequest, graphql: Graphql): Promise<ConsoleCatalog | ConsoleRunDetail | ConsoleActionResult> {
  async function catalogRaw(requireRepository = true) {
    const data = await graphql(CONSOLE_CATALOG_QUERY, { selector }); const repository = row(data.repositoryOrError);
    if (requireRepository && repository.__typename !== "Repository") throw new ConsoleServiceError(502);
    const mutations = new Set(list(row(row(data.__schema).mutationType).fields).map(value => string(row(value).name)));
    return { repository, mutations };
  }
  async function requestRun(requestId: string) {
    const matches = runs(await graphql(FIND_REQUEST, { filter: filter({ tags: [{ key: "kortravel/admin-request", value: consoleRequestId(requestId) }] }) }));
    if (matches.length > 1) throw new ConsoleServiceError(409);
    return matches[0];
  }
  const accepted = (run: Row, requestId: string, replayed = false): ConsoleActionResult => {
    if (!uuid(run.runId)) throw new ConsoleServiceError(502);
    return { state: run.status === "NOT_STARTED" ? "unknown" : "accepted", message: run.status === "NOT_STARTED" ? "요청은 생성됐지만 예약 시작이 확인되지 않았습니다. 같은 요청으로 확인·복구할 수 있습니다." : CONSOLE_ACTIVE.has(status(run.status)) ? "실행 요청을 확인했습니다. 대기·실행 상태를 아래에서 확인하세요." : "요청에 연결된 실행을 확인했습니다. 자료 수집 결과는 실행 상세에서 확인하세요.", runId: string(run.runId), requestId, replayed };
  };
  if (input.operation === "requestStatus") { const prior = await requestRun(input.requestId); return prior ? accepted(prior, input.requestId, true) : { state: "unknown", message: "이 요청의 실행을 아직 확인하지 못했습니다. 변경 요청은 다시 보내지 않았습니다.", requestId: input.requestId }; }
  if (input.operation === "catalog") {
    const { repository, mutations } = await catalogRaw();
    const capabilities = { launch: mutations.has("launchIdempotentRun"), retry: mutations.has("launchIdempotentRun"), cancel: mutations.has("terminateRun"), schedule: mutations.has("startSchedule") || mutations.has("stopRunningSchedule"), sensor: mutations.has("startSensor") || mutations.has("stopSensor") };
    const instigators = (key: "schedules" | "sensors") => list(repository[key]).slice(0,300).map(row).filter(item => identifier(item.name)).map(item => { const state = row(item[key === "schedules" ? "scheduleState" : "sensorState"]); return { name: string(item.name), status: effectiveState(item,state), canStart: state.hasStartPermission === true && mutations.has(key === "schedules" ? "startSchedule" : "startSensor"), canStop: state.hasStopPermission === true && mutations.has(key === "schedules" ? "stopRunningSchedule" : "stopSensor"), ...(key === "schedules" ? { jobName: identifier(item.pipelineName) ?? undefined, cron: typeof item.cronSchedule === "string" && item.cronSchedule.length <= 120 ? item.cronSchedule : undefined } : {}) }; });
    return { jobs: list(repository.jobs).slice(0,300).map(row).filter(item => identifier(item.name) && !string(item.name).startsWith("__") && item.hasLaunchExecutionPermission === true).map(item => string(item.name)), schedules: instigators("schedules"), sensors: instigators("sensors"), capabilities, checkedAt: new Date().toISOString() };
  }
  if (input.operation === "detail") {
    const run = requiredRun(await graphql(CONSOLE_RUN_QUERY, { filter: runFilter(input.runId) }), input.runId);
    if (input.includeEvents === false) return sanitizeConsoleRun(run);
    try { const events = requiredRun(await graphql(CONSOLE_EVENTS_QUERY, { filter: runFilter(input.runId), cursor: input.cursor ?? null }), input.runId); return sanitizeConsoleRun(run,events); }
    catch { return { ...sanitizeConsoleRun(run), events: [{ kind: "UNAVAILABLE", time: null, step: null, title: "이벤트 내역 조회 지연", detail: "실행·단계 상태는 확인했습니다. 이벤트 내역은 잠시 후 다시 조회하세요.", warning: true, counts: {} }] }; }
  }
  const { repository, mutations } = await catalogRaw(input.action !== "cancel");
  if (input.action === "cancel") {
    const runId = input.runId!; const run = requiredRun(await graphql(BASIC_RUN_QUERY, { filter: runFilter(runId) }),runId);
    if (run.status === "CANCELING") return { state: "unchanged", message: "이미 취소 처리 중입니다. 현재 상태를 새로 확인해 주세요.", runId };
    if (!CONSOLE_ACTIVE.has(status(run.status))) return { state: "unchanged", message: "이미 종료된 실행입니다. 현재 상태를 새로 확인해 주세요.", runId };
    if (!mutations.has("terminateRun") || run.canTerminate !== true || run.hasTerminatePermission !== true) return { state: "blocked", message: "이 실행에 정상 취소를 요청할 수 없습니다.", runId };
    try { const result = row((await graphql(CANCEL,{ runId })).result); if (result.__typename === "TerminateRunSuccess" && row(result.run).runId === runId) return { state: "accepted", message: "정상 취소를 요청했습니다. 취소 완료 여부는 실행 상태로 확인하세요.", runId }; return { state: !result.__typename || result.__typename === "PythonError" ? "unknown" : "rejected", message: "취소 요청 결과를 확정하지 못했습니다. 실행 상태를 확인해 주세요.", runId }; }
    catch { return { state: "unknown", message: "취소 결과가 확인되지 않았습니다. 실행 상태만 새로 조회하세요.", runId }; }
  }
  if (input.action === "launch" || input.action === "retry") {
    let job = input.name!;
    if (input.action === "retry") { const old = requiredRun(await graphql(BASIC_RUN_QUERY,{ filter:runFilter(input.runId!) }),input.runId!); if (!["FAILURE","CANCELED"].includes(status(old.status)) || old.hasReExecutePermission !== true) return { state:"blocked",message:"실패·취소된 실행만 같은 작업으로 다시 실행할 수 있습니다." }; job=string(old.jobName); }
    const prior = await requestRun(input.requestId);
    if (prior && prior.jobName !== job) return { state:"rejected",message:"다른 작업에 사용한 요청 ID입니다. 실행 상태를 확인해 주세요." };
    if (prior && prior.status !== "NOT_STARTED") return accepted(prior,input.requestId,true);
    const preserveOrReject = (state: "blocked" | "rejected", message: string, extra: Pick<ConsoleActionResult,"runId" | "requestId"> = {}): ConsoleActionResult => {
      if(prior)return {...accepted(prior,input.requestId,true),state:"unknown",message:`${message} 기존 예약의 시작·해제가 확인되지 않아 같은 요청을 유지합니다.`};
      return {state,message,...extra};
    };
    const target = list(repository.jobs).map(row).find(item => item.name === job && item.hasLaunchExecutionPermission === true && !job.startsWith("__"));
    if (!target || !mutations.has("launchIdempotentRun")) return preserveOrReject("blocked","이 작업을 실행할 권한 또는 서버 기능이 없습니다.");
    const active = runs(await graphql(ACTIVE_JOB,{ filter:filter({ pipelineName:job,statuses:[...CONSOLE_ACTIVE] }) })).find(item=>item.runId!==prior?.runId);
    if (active) return preserveOrReject("blocked","같은 작업이 이미 대기 또는 실행 중입니다. 기존 실행에서 진행 상황을 확인하세요.",uuid(active.runId)?{runId:string(active.runId)}:{});
    const nativeId=consoleRequestId(input.requestId);
    try {
      const result=row((await graphql(LAUNCH,{ requestId:nativeId, executionParams:{ selector:{...selector,pipelineName:job}, runConfigData:{}, executionMetadata:{ tags:[{key:".dagster/repository",value:`${DAGSTER_REPOSITORY_NAME}@${DAGSTER_LOCATION_NAME}`},{key:"dagster/code_location",value:DAGSTER_LOCATION_NAME},{key:"kortravel/admin-request",value:nativeId},...(input.action==="retry"?[{key:"kortravel/admin-retry-of",value:input.runId!}]:[])] } } })).result);
      const run=row(result.run);
      if (result.__typename !== "IdempotentLaunchRunSuccess") {
        const rejection=launchRejections[string(result.__typename)];
        if(rejection)return preserveOrReject("rejected",rejection,{requestId:input.requestId});
        return {state:"unknown",message:"실행 예약 결과를 확인하지 못했습니다. 요청 상태를 조회하세요.",requestId:input.requestId};
      }
      const owned=list(run.tags).some(value=>row(value).key===tag.key&&row(value).value===tag.value);
      if(!owned) return {state:"unknown",message:"이 프로젝트의 실행 예약 여부를 확인하지 못했습니다. 요청 상태를 조회하세요.",requestId:input.requestId};
      return accepted(run,input.requestId,result.replayed===true);
    } catch { return { state:"unknown",message:"실행 예약 결과가 확인되지 않았습니다. 새 요청을 만들지 말고 요청 상태를 확인하세요.",requestId:input.requestId }; }
  }
  const sensor=input.action.endsWith("Sensor"), starting=input.action.startsWith("start");
  const target=list(repository[sensor?"sensors":"schedules"]).map(row).find(item=>item.name===input.name);
  if(!target) throw new ConsoleServiceError(404);
  const state=row(target[sensor?"sensorState":"scheduleState"]), current=effectiveState(target,state);
  if(current===(starting?"RUNNING":"STOPPED")) return {state:"unchanged",message:starting?"이미 시작된 예약입니다.":"이미 중지된 예약입니다."};
  const mutation=sensor?(starting?"startSensor":"stopSensor"):(starting?"startSchedule":"stopRunningSchedule");
  if(!mutations.has(mutation)||state[starting?"hasStartPermission":"hasStopPermission"]!==true) return {state:"blocked",message:"이 예약을 변경할 권한 또는 서버 기능이 없습니다."};
  try {
    const query=sensor?(starting?START_SENSOR:STOP_SENSOR):(starting?START_SCHEDULE:STOP_SCHEDULE);
    const variables=starting?{selector:{...selector,[sensor?"sensorName":"scheduleName"]:input.name}}:{id:string(state.id)};
    if(!starting&&!variables.id) throw new ConsoleServiceError(502);
    const result=row((await graphql(query,variables)).result);
    const success=starting&&sensor?result.__typename==="Sensor":sensor?result.__typename==="StopSensorMutationResult":result.__typename==="ScheduleStateResult";
    return success?{state:"accepted",message:starting?"예약을 시작했습니다. 다음 평가 시각부터 적용됩니다.":"예약을 중지했습니다. 이미 실행 중인 수집은 계속 진행합니다."}:{state:!result.__typename||result.__typename==="PythonError"?"unknown":"rejected",message:"예약 변경 결과를 확인하지 못했습니다. 현재 상태를 확인해 주세요."};
  } catch { return {state:"unknown",message:"예약 변경 결과가 확인되지 않았습니다. 예약 상태만 새로 조회하세요."}; }
}
