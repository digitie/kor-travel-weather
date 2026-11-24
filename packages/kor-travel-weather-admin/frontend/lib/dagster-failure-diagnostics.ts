/** 실패 원문은 서버에서 고정 코드·허용된 숫자/분류로 축약한다. URL·SQL·키는 반환하지 않는다. */
type ObjectValue = Record<string, unknown>;
export type FailureEvent = { __typename: string; message?: string; stepKey?: string | null; diagnostic?: boolean };
const object = (value: unknown): ObjectValue => value !== null && typeof value === "object" && !Array.isArray(value) ? value as ObjectValue : {};
const text = (value: unknown) => typeof value === "string" ? value : "";

export const GRID_FAILURE_LABELS:Record<string,string>={PROVIDER_NETWORK:"제공기관 연결 실패",PROVIDER_SERVER:"제공기관 서버 오류",PROVIDER_PARSE:"제공기관 응답 해석 실패",PROVIDER_NO_DATA:"제공기관 자료 없음",DB_LOCK_CONTENTION:"DB 잠금 경합"};

/** JSON metadata 중 고정된 격자 오류 건수만 읽는다. 임의 키·값·원문은 반환하지 않는다. */
export function gridFailureCounts(raw:unknown):Record<string,number> {
  if(!Array.isArray(raw))return {};
  const entry=raw.slice(0,64).map(object).find(value=>value.label==="grid_failure_counts");
  const encoded=entry?.jsonString;
  if(typeof encoded!=="string"||encoded.length>4096||new TextEncoder().encode(encoded).byteLength>4096)return {};
  try{
    const parsed=object(JSON.parse(encoded)),counts:Record<string,number>={};
    for(const key of Object.keys(GRID_FAILURE_LABELS)){
      const value=parsed[key];
      if(typeof value==="number"&&Number.isInteger(value)&&value>=0&&value<=1_000_000_000)counts[key]=value;
    }
    return counts;
  }catch{return {};}
}

const reasons: Record<string, [string, string]> = {
  COLLECTION_DEFERRED: ["현재 수집이 진행 중이거나 수집 주기에 따라 이번 실행을 이월했습니다.", "기존 실행과 다음 수집 시각을 확인하세요. 기존 정상 자료는 유지합니다."],
  DERIVED_CACHE_DEFERRED: ["관측 자료는 저장했지만 파생 조회 캐시 갱신이 지연됐습니다.", "저장 자료는 유지합니다. 다음 캐시 갱신 결과를 확인하세요."],
  REFERENCE_EXPORT_DEFERRED: ["기준자료의 파생 읽기 본문 준비가 지연됐습니다.", "마지막 정상 읽기 본문을 유지합니다. 다음 준비 실행을 확인하세요."],
  ALERT_PAGE_METADATA: ["특보 응답의 페이지 번호·크기·전체 행 수가 완결성 조건과 다릅니다.", "페이지 진단값과 제공기관 응답 규격을 확인하세요. 잘못된 결과는 게시하지 않았습니다."],
  ALERT_PAGE_ROWS: ["특보 페이지의 선언된 행 수와 실제 행 수가 다릅니다.", "원천 응답을 확인하세요. 마지막 정상 자료를 유지합니다."],
  ALERT_PAGE_TOTAL_CHANGED: ["특보 페이지 조회 중 전체 행 수가 바뀌었습니다.", "다음 정기 수집에서 다시 확인합니다. 이번 불완전 결과는 게시하지 않았습니다."],
  ALERT_PAGE_DUPLICATE: ["특보 페이지에 같은 발표가 중복됐습니다.", "원천 페이지와 중복 식별자를 확인해야 합니다."],
  ALERT_QUARANTINE_LIMIT: ["같은 특보 발표 관측에 내용이 다른 행이 격리 상한보다 많습니다.", "원천 응답 자체를 믿을 수 없어 이번 결과를 게시하지 않았습니다. 격리 행 수와 상한을 확인하세요. 마지막 정상 자료를 유지합니다."],
  ALERT_FIRST_PAGE_CHANGED: ["전체 조회 중 특보 첫 페이지 내용이 바뀌었습니다.", "다음 정기 수집에서 다시 확인합니다. 마지막 정상 자료를 유지합니다."],
  ALERT_TOO_MANY_ROWS: ["특보 응답이 수집 행·페이지 상한을 넘었습니다.", "원천 범위와 수집 상한을 확인하세요. 상한을 자동으로 우회하지 않습니다."],
  ALERT_RESPONSE_TOO_LARGE: ["특보 응답이 허용 크기를 넘었습니다.", "원천 응답 크기와 수집 범위를 확인하세요."],
  ALERT_RUN_OWNERSHIP_LOST: ["특보 수집 실행의 소유권이 회수됐습니다.", "기존 작업자 상태를 먼저 확인하세요. 중복 실행하지 않습니다."],
  ALERT_PROVIDER_AUTH: ["기상청 인증 또는 서비스 접근 권한이 거절됐습니다.", "활용신청·서비스 키 권한을 확인해야 합니다. 즉시 반복 호출하지 않습니다."],
  ALERT_PROVIDER_RATE_LIMIT: ["기상청 호출 한도에 도달했습니다.", "한도 종류와 복구 정책을 확인하세요. 초당 한도는 호출 보호 후, 일일 한도는 초기화 이후에 다시 수집합니다."],
  ALERT_PROVIDER_UNAVAILABLE: ["기상청 네트워크 또는 제공기관 요청에 실패했습니다.", "제공기관 상태를 확인하고 다음 정기 실행 결과를 확인하세요."],
  ALERT_TIMEOUT: ["특보 요청 또는 게시가 실행 시간 예산을 넘었습니다.", "다음 정기 실행에서 재시도합니다. 반복되면 단계별 시간을 확인하세요."],
  ALERT_COLLECTION_CANCELLED: ["특보 수집이 취소됐습니다.", "취소 요청과 실행 소유권을 확인하세요."],
  ALERT_COLLECTION_FAILED: ["특보 수집을 완료하지 못했습니다.", "실행 단계와 오류 유형을 확인하세요. 원문은 별도 Dagster에서 확인할 수 있습니다."],
  KMA_ALL_GRIDS_FAILED: ["이번 기상청 조회 대상 격자의 수집이 모두 미완료입니다.", "원인별 건수와 제공기관·DB 상태를 확인하고 다음 수집에서 재개하세요. 마지막 정상 자료를 빈 결과로 바꾸지 않습니다."],
  KMA_COLLECTION_FAILED: ["기상청 격자 자료의 요청·정규화·게시 중 수집을 완료하지 못했습니다.", "실패 단계와 단계별 소요 시간·저장 건수를 확인하세요. 마지막 정상 자료를 유지하며 호출 보호 후 다음 정기 실행에서 재시도합니다."],
  KMA_ALL_VALUES_SKIPPED: ["기상청에서 받은 모든 값이 결측·범위 밖 값으로 건너뛰어졌습니다.", "시도·건너뜀·결측·범위 밖 건수와 원천 상태를 확인하세요. 빈 결과로 마지막 정상 자료를 바꾸지 않습니다."],
  TRAFFIC_FLOW_INVALID_RECORDS: ["고속도로 소통 응답에 해석할 수 없는 행이 있습니다.", "수신·파싱·불량 행 수와 제공기관 필드 규격을 확인하세요. 정상 저장분은 유지합니다."],
  TRAFFIC_FLOW_COUNT: ["고속도로 소통의 선언 건수와 실제 전체 목록이 다릅니다.", "불완전한 목록으로 현재 자료를 바꾸지 않습니다. 다음 정기 실행을 확인하세요."],
  RETENTION_BACKLOG: ["단기예보 정리 backlog가 24시간 이상 뒤처진 채 연속해서 줄지 않았습니다.", "정리 처리량과 DB 대기를 확인하세요. 데이터 삭제 범위를 임의로 늘리지 않습니다."],
  PROVIDER_AUTH: ["제공기관 인증 또는 접근 권한이 거절됐습니다.", "서비스 활용신청·인증 설정을 확인하세요. 즉시 반복 호출하지 않습니다."],
  PROVIDER_RATE_LIMIT: ["제공기관 호출 한도에 도달했습니다.", "한도 종류와 복구 정책을 확인하세요. 초당 한도는 호출 보호 후, 일일 한도는 초기화 이후에 다시 수집합니다."],
  REQUEST_TIMEOUT: ["제공기관 요청이 제한 시간 안에 끝나지 않았습니다.", "다음 정기 실행에서 재시도합니다. 반복되면 제공기관 응답과 네트워크를 확인하세요."],
  DATABASE_RETRYABLE: ["DB 잠금 경합 또는 일시적인 저장 시간 초과가 발생했습니다.", "저장 재시도 결과와 DB 대기를 확인하세요. 외부 자료를 중복 수집하지 않습니다."],
  PARTIAL_SOURCE_FAILURE: ["일부 원천 또는 데이터셋 수집에 실패했습니다.", "성공 저장분과 마지막 정상 자료를 유지합니다. 실패한 원천의 후속 실행을 확인하세요."],
  OPINET_BROWSER_TIMEOUT: ["오피넷 브라우저의 지역·유종 조회가 제한 시간을 넘었습니다.", "저장한 정상 결과와 호출 보호를 유지합니다. 다음 정기 재시도 결과를 확인하세요."],
  COLLECTION_TIMEOUT: ["수집 실행이 허용된 시간 예산을 넘었습니다.", "실패 단계와 처리 건수를 확인하세요. 호출 보호 간격을 지켜 재시도합니다."],
  COLLECTION_FAILED: ["수집을 완료하지 못했습니다.", "실패 단계와 성공 저장 건수를 확인하세요. 성공 저장분은 유지합니다."],
  VADAHUB_SCHEMA_CHANGED: ["배편 기준자료의 공식 파일 형식이 기존 규격과 달라졌습니다.", "실패한 데이터셋의 파일 규격을 확인해야 합니다. 마지막 정상 자료는 보존합니다."],
  VADAHUB_SOURCE_FAILED: ["배편 기준자료의 일부 원천을 읽지 못했습니다.", "실패한 데이터셋을 확인하고 호출 보호 이후 후속 실행을 확인하세요."],
  UNKNOWN_FAILURE: ["등록되지 않은 실행 오류입니다. 원문은 보안상 이 화면에 전달하지 않습니다.", "실행 ID로 Dagster 원본을 확인하세요. 확인 전 무제한 재시도하지 않습니다."],
};
const stages: Record<string, string> = { run_start: "실행 시작", page_request: "원천 페이지 요청", page_validation: "페이지 완결성 검사", publication: "자료 게시", fetch: "원천 요청", normalize: "자료 정규화", publish: "DB 게시", storage: "DB 저장", provider_parse: "제공기관 응답 해석", provider_fetch: "제공기관 요청", collection: "수집 실행", validation: "원천 자료 규격 검사" };
const steps: Record<string, string> = { kma_regional_alert_events_sync: "지역별 기상특보 수집", collect_highway_transport: "고속도로 소통·돌발 수집", collect_fuel_transport: "주유소·유가 수집", collect_vadahub_reference: "배편 기준자료 수집", weather_short_forecast_retention: "단기예보 정리" };
const numbers: Record<string, string> = { planned_grids: "계획 격자 수", failed_grids: "미완료 격자 수", deferred_grids: "재개 대기 격자 수", values_attempted: "시도 값 수", values_skipped: "건너뛴 값 수", missing_values: "결측 값 수", invalid_values: "범위 밖 값 수", requested_page: "요청 페이지", returned_page: "반환 페이지", page_size: "페이지 크기", total_count: "전체 행 수", expected_rows: "예상 행 수", returned_rows: "반환 행 수", requests_fetched: "요청 횟수", http_status: "HTTP 상태", quarantined_rows: "격리 행 수", quarantine_limit: "격리 상한", received_rows: "수신 행 수", parsed_rows: "파싱 행 수", invalid_rows: "불량 행 수", fetch_ms: "요청 소요(ms)", normalize_ms: "정규화 소요(ms)", publish_ms: "게시 소요(ms)", published_values: "저장 값 수", completed_grids: "완료 격자 수", run_id: "저장 실행 ID", error_count: "오류 수", raw_response_count: "저장 원문 수", traffic_snapshot_count: "소통 저장 수", incident_snapshot_count: "돌발 저장 수", fuel_station_count: "주유소 저장 수", fuel_price_count: "가격 저장 수", failed_dataset_count: "실패 데이터셋 수", successful_dataset_count: "성공 데이터셋 수" };
const fieldNames = new Set(["totalCount", "pageNo", "numOfRows"]);
const fieldTypes = new Set(["null", "boolean", "integer", "number", "string", "object", "array"]);
const classNames = new Set(["Failure", "AlertCollectionError", "KrexParseError", "TimeoutError", "RuntimeError", "ValueError", "KeyError", "TypeError", "OperationalError", "IntegrityError", "DagsterExecutionStepExecutionError", "SchemaError", "ValidationError"]);

const values: Record<string, Record<string, string>> = {
  rate_limit_scope: { daily: "일일 호출 한도", per_second: "초당 호출 한도" },
  dataset: { passenger_routes: "연안여객 항로", ferry_routes: "도선 항로", ferry_facilities: "도선 시설", official_terminals: "공식 여객터미널 파일" },
  scope: { highway: "고속도로", fuel: "유가" },
  status: { failed: "실패", failure: "실패", partial_success: "일부 실패", skipped: "건너뜀" },
  recovery_hint: { CHECK_RUN_DETAIL: "실행 상세 확인", CHECK_RESPONSE_CONTRACT: "응답 규격 확인 후 재시도", RETRY_AFTER_BACKOFF: "호출 보호 시간 이후 재시도", WAIT_QUOTA_RESET: "일일 한도 초기화 이후 재시도" },
};
function errorChain(raw: unknown): ObjectValue[] {
  const chain: ObjectValue[] = [];
  for (let value = object(raw); Object.keys(value).length && chain.length < 4; value = object(value.cause)) chain.push(value);
  return chain;
}
function metadata(raw: unknown): ObjectValue {
  const result: ObjectValue = {};
  const entries = object(raw).metadataEntries;
  if (!Array.isArray(entries)) return result;
  for (const value of entries.slice(0, 64)) {
    const entry = object(value), label = text(entry.label);
    const number = entry.intValue ?? entry.floatValue;
    if ((label === "retryable" || label === "successful_data_preserved") && typeof entry.boolValue === "boolean") result[label] = entry.boolValue;
    else if (Object.hasOwn(values, label) && Object.hasOwn(values[label], text(entry.text))) result[label] = entry.text;
    else if (label === "error_type" && classNames.has(text(entry.text))) result[label] = entry.text;
    else if (Object.hasOwn(numbers, label) && typeof number === "number" && Number.isFinite(number) && number >= 0 && number <= 1_000_000_000) result[label] = number;
    else if (label === "error_code" && Object.hasOwn(reasons, text(entry.text))) result[label] = entry.text;
    else if (label === "stage" && Object.hasOwn(stages, text(entry.text))) result[label] = entry.text;
    else if (label === "metadata_field" && fieldNames.has(text(entry.text))) result[label] = entry.text;
    else if (label === "metadata_type" && fieldTypes.has(text(entry.text))) result[label] = entry.text;
  }
  return result;
}
function codeFor(message: string, data: ObjectValue): string {
  if (typeof data.error_code === "string") return data.error_code;
  const known = Object.keys(reasons).find(code => code !== "UNKNOWN_FAILURE" && new RegExp(`\\b${code}\\b`).test(message));
  if (known) return known;
  if (/traffic flow response contains invalid records/.test(message)) return "TRAFFIC_FLOW_INVALID_RECORDS";
  if (/traffic flow count must match|incomplete total_count/.test(message)) return "TRAFFIC_FLOW_COUNT";
  if (/backlog.*24시간|24시간.*backlog/.test(message)) return "RETENTION_BACKLOG";
  if (/quota|rate.limit|429/i.test(message)) return "PROVIDER_RATE_LIMIT";
  if (/AuthError|인증.*거절|등록되지 않은 서비스키/.test(message)) return "PROVIDER_AUTH";
  if (/deadlock detected|LockNotAvailable|lock timeout|statement timeout/.test(message)) return "DATABASE_RETRYABLE";
  if (/Opinet.*TimeoutError|Opinet.*timeout/i.test(message)) return "OPINET_BROWSER_TIMEOUT";
  if (/TimeoutError|timed out|시간.*초과/.test(message)) return "REQUEST_TIMEOUT";
  if (/partial_success|일부.*수집.*실패|모든 dataset 수집이 실패/.test(message)) return "PARTIAL_SOURCE_FAILURE";
  return "UNKNOWN_FAILURE";
}

/** 임의 예외 문자열을 복사하지 않고 안전한 오류 분류와 숫자만 새 문자열로 만든다. */
export function failureDiagnostic(raw: unknown): string {
  const event = object(raw), chain = errorChain(event.error), data = metadata(event.failureMetadata);
  const input = [text(event.message), ...chain.flatMap(item => [text(item.className), text(item.message)])].map(value => value.slice(0, 8192)).join("\n");
  for (const key of ["received_rows", "parsed_rows", "invalid_rows"]) {
    const match = input.match(new RegExp(`\\b${key}=([0-9]{1,9})\\b`));
    if (match && data[key] === undefined) data[key] = Number(match[1]);
  }
  const code = codeFor(input, data), [reason, retry] = reasons[code];
  const lines = [`오류 코드: ${code}`, `실패 단계: ${(Object.hasOwn(stages, text(data.stage)) ? stages[text(data.stage)] : Object.hasOwn(steps, text(event.stepKey)) ? steps[text(event.stepKey)] : "작업 실행")}`, `원인: ${reason}`, `대응: ${retry}`];
  const classes = [...new Set(chain.map(item => text(item.className)).filter(name => classNames.has(name)))];
  if (classes.length) lines.push(`오류 유형: ${classes.join(" → ")}`);
  if (data.error_type && !classes.includes(text(data.error_type))) lines.push(`원천 오류 유형: ${data.error_type}`);
  for (const [key, mapping] of Object.entries(values)) if (data[key]) lines.push(`${({ dataset: "실패 데이터셋", scope: "수집 범위", status: "저장 결과", recovery_hint: "복구 정책", rate_limit_scope: "호출 한도 종류" } as Record<string, string>)[key]}: ${mapping[text(data[key])]}`);
  if (typeof data.retryable === "boolean") lines.push(`재시도 가능: ${data.retryable ? "예 · 호출 보호 후 정기 실행" : "아니오 · 복구 정책 확인"}`);
  if (typeof data.successful_data_preserved === "boolean") lines.push(`성공 저장분 보존: ${data.successful_data_preserved ? "예" : "확인 필요"}`);
  if (data.metadata_field) lines.push(`검사 필드: ${data.metadata_field} · 응답 형식: ${data.metadata_type ?? "미제공"}`);
  for (const [key, label] of Object.entries(numbers)) if (data[key] !== undefined) lines.push(`${label}: ${data[key]}`);
  for(const [code,count] of Object.entries(gridFailureCounts(object(event.failureMetadata).metadataEntries))) lines.push(`격자 원인 · ${GRID_FAILURE_LABELS[code]}: ${count}`);
  return lines.join("\n");
}

/** 선택한 실행의 필요한 이벤트만 반환한다. 원문 error/stack/metadata는 브라우저에 도달하지 않는다. */
export function sanitizeFailureResponse(raw: unknown): ObjectValue {
  const payload = object(raw);
  if (Array.isArray(payload.errors) && payload.errors.length) return { errors: [{ message: "실행 오류 상세를 읽지 못했습니다." }] };
  const runs = object(object(payload.data).runsOrError);
  if (runs.__typename !== "Runs") return { data: { runsOrError: { __typename: "PythonError" } } };
  const results = Array.isArray(runs.results) ? runs.results.slice(0, 1) : [];
  if (results.some(value => !Array.isArray(object(object(value).eventConnection).events))) return { errors: [{ message: "선택한 실행의 이벤트 형식을 확인하지 못했습니다." }] };
  return { data: { runsOrError: { __typename: "Runs", results: results.map(value => {
    const connection = object(object(value).eventConnection);
    const events = Array.isArray(connection.events) ? connection.events.slice(0, 1000) : [];
    return { eventConnection: { cursor: typeof connection.cursor === "string" && connection.cursor.length <= 512 ? connection.cursor : null, hasMore: connection.hasMore === true,
      events: events.map(value => {
        const event = object(value), failure = event.__typename === "ExecutionStepFailureEvent" || event.__typename === "RunFailureEvent";
        return failure ? { __typename: event.__typename, message: failureDiagnostic(event), diagnostic: true } : { __typename: "Event" };
      }) } };
  }) } } };
}

/** 개요는 필요한 상태와 경고 태그만 내보내고 임의 run 태그·native 오류 문장을 숨긴다. */
export function sanitizeDagsterOverview(raw:unknown):ObjectValue {
  const payload=object(raw);
  if(Array.isArray(payload.errors)&&payload.errors.length)return {errors:[{message:"Dagster 개요를 읽지 못했습니다."}]};
  const data=object(payload.data),result:ObjectValue={};
  for(const key of ["repositoryOrError","runsOrError","activeRuns"]){
    if(data[key]===undefined)continue;
    const value=object(data[key]);
    if(key==="repositoryOrError"){
      result[key]=value.__typename==="Repository"?value:{__typename:value.__typename,message:"작업 카탈로그를 확인하지 못했습니다."};
    }else if(value.__typename!=="Runs")result[key]={__typename:value.__typename,message:"실행 목록을 확인하지 못했습니다."};
    else result[key]={__typename:"Runs",results:(Array.isArray(value.results)?value.results:[]).slice(0,1000).map(rawRun=>{
      const run=object(rawRun),tags=Array.isArray(run.tags)?run.tags:[];
      return {runId:run.runId,status:run.status,jobName:run.jobName,startTime:run.startTime,endTime:run.endTime,tags:tags.map(object).filter(tag=>tag.key==="kortravel/collection-outcome"&&["partial","deferred"].includes(text(tag.value))||tag.key==="dagster/max_runtime"&&/^\d{1,7}$/.test(text(tag.value))).map(tag=>({key:tag.key,value:tag.value}))};
    })};
  }
  return {data:result};
}
