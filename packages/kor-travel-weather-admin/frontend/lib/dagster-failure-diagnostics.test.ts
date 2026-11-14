import { describe, expect, it } from "vitest";
import { failureDiagnostic, sanitizeFailureResponse } from "./dagster-failure-diagnostics";

const event = {
  __typename: "ExecutionStepFailureEvent", stepKey: "kma_regional_alert_events_sync", message: "step failed",
  error: { className: "DagsterExecutionStepExecutionError", message: "wrapper", cause: { className: "AlertCollectionError", message: "ALERT_PAGE_METADATA" } },
  failureMetadata: { metadataEntries: [
    { label: "error_code", text: "ALERT_PAGE_METADATA" }, { label: "stage", text: "page_validation" },
    { label: "requested_page", intValue: 1 }, { label: "metadata_field", text: "pageNo" }, { label: "metadata_type", text: "null" },
    { label: "total_count", intValue: 0 },
  ] },
};

describe("수집 실패 상세의 서버 정제", () => {
  it("단계·근본 원인·분류·0을 포함한 실제 페이지 진단을 제공한다", () => {
    const result = failureDiagnostic(event);
    expect(result).toContain("ALERT_PAGE_METADATA");
    expect(result).toContain("페이지 완결성 검사");
    expect(result).toContain("AlertCollectionError");
    expect(result).toContain("요청 페이지: 1");
    expect(result).toContain("전체 행 수: 0");
    expect(result).toContain("검사 필드: pageNo · 응답 형식: null");
  });
  it("기존 예외 chain의 안전한 행 수를 읽고 응답 규격 확인을 안내한다", () => {
    const result = failureDiagnostic({ error: { className: "Failure", message: "traffic flow response contains invalid records (received_rows=8352, parsed_rows=8351, invalid_rows=1)" } });
    expect(result).toContain("TRAFFIC_FLOW_INVALID_RECORDS");
    expect(result).toContain("수신 행 수: 8352");
    expect(result).toContain("불량 행 수: 1");
    expect(result).toContain("정상 저장분은 유지");
  });
  it("scope·재시도·성공 저장분과 단계 수치를 고정 규격으로 표시한다", () => {
    const result = failureDiagnostic({ failureMetadata: { metadataEntries: [
      { label: "error_code", text: "COLLECTION_TIMEOUT" }, { label: "scope", text: "fuel" },
      { label: "stage", text: "provider_fetch" }, { label: "retryable", boolValue: true },
      { label: "successful_data_preserved", boolValue: true }, { label: "publish_ms", floatValue: 150.25 },
      { label: "fuel_price_count", intValue: 0 },
    ] } });
    expect(result).toContain("제공기관 요청");
    expect(result).toContain("수집 범위: 유가");
    expect(result).toContain("재시도 가능: 예");
    expect(result).toContain("성공 저장분 보존: 예");
    expect(result).toContain("게시 소요(ms): 150.25");
    expect(result).toContain("가격 저장 수: 0");
  });
  it("auth·한도·timeout에 서로 다른 대응을 보여준다", () => {
    expect(failureDiagnostic({ message: "ALERT_PROVIDER_AUTH" })).toContain("활용신청·서비스 키 권한");
    expect(failureDiagnostic({ message: "ALERT_PROVIDER_RATE_LIMIT" })).toContain("호출 보호 시간");
    expect(failureDiagnostic({ message: "Opinet browser action failed: cause=TimeoutError" })).toContain("OPINET_BROWSER_TIMEOUT");
  });
  it("URL·키·SQL·HTML·임의 metadata를 응답에 그대로 전달하지 않는다", () => {
    const secret = "fixture-unlabelled-private-key";
    const result = sanitizeFailureResponse({ errors: undefined, data: { runsOrError: { __typename: "Runs", results: [{ eventConnection: {
      cursor: "cursor", hasMore: false, events: [{ ...event,
        message: `<script>${secret}</script> https://example.invalid/?key=${secret} SELECT * FROM private_table`,
        error: { className: secret, message: secret, stack: [secret], cause: { message: `ALERT_PAGE_METADATA ${secret}` } },
        failureMetadata: { metadataEntries: [
          { label: "raw_payload", text: secret }, { label: "stage", text: secret },
          { label: "metadata_field", text: secret }, { label: "requested_page", intValue: -1 },
          { label: "page_size", floatValue: Infinity }, { label: "total_count", intValue: 1_000_000_001 },
        ] },
      }] } }] } } });
    const serialized = JSON.stringify(result);
    for (const value of [secret, "https://", "<script>", "SELECT", "raw_payload", '"stack"', '"error"']) expect(serialized).not.toContain(value);
    expect(serialized).toContain("ALERT_PAGE_METADATA");
    expect(serialized).toContain('"diagnostic":true');
    expect(serialized).not.toContain("요청 페이지");
  });
  it("새 오류는 고정된 미등록 안내를 주고 위조한 class·stage는 숨긴다", () => {
    const result = failureDiagnostic({ message: "unknown-secret", stepKey: "constructor", error: { className: "unknown-secret" }, failureMetadata: { metadataEntries: [{ label: "error_code", text: "__proto__" }] } });
    expect(result).toContain("UNKNOWN_FAILURE");
    expect(result).toContain("실패 단계: 작업 실행");
    expect(result).not.toContain("unknown-secret");
    expect(result).not.toContain("function");
  });
  it("잘못된 이벤트 연결을 빈 정상 결과로 꾸미지 않는다", () => {
    expect(sanitizeFailureResponse({ data: { runsOrError: { __typename: "Runs", results: [{}] } } })).toHaveProperty("errors");
    expect(sanitizeFailureResponse({ errors: [{ message: "provider private-key" }] })).toEqual({ errors: [{ message: "실행 오류 상세를 읽지 못했습니다." }] });
  });
});

it("배편 파일 규격 실패의 원천과 보존·건수를 표시한다", () => {
  const result = failureDiagnostic({ failureMetadata: { metadataEntries: [
    { label: "error_code", text: "VADAHUB_SCHEMA_CHANGED" }, { label: "stage", text: "validation" },
    { label: "error_type", text: "SchemaError" }, { label: "dataset", text: "official_terminals" },
    { label: "failed_dataset_count", intValue: 1 }, { label: "successful_dataset_count", intValue: 3 },
    { label: "successful_data_preserved", boolValue: true },
  ] } });
  expect(result).toContain("VADAHUB_SCHEMA_CHANGED");
  expect(result).toContain("원천 자료 규격 검사");
  expect(result).toContain("실패 데이터셋: 공식 여객터미널 파일");
  expect(result).toContain("실패 데이터셋 수: 1");
  expect(result).toContain("성공 데이터셋 수: 3");
  expect(result).toContain("성공 저장분 보존: 예");
});


it("기상청 격자 수집 고정 코드는 단계·시간을 보존하며 원문을 숨긴다", () => {
  const secret = "fixture-kma-private-key";
  const result = JSON.stringify(sanitizeFailureResponse({ data: { runsOrError: { __typename: "Runs", results: [{ eventConnection: { hasMore: false, events: [{
    __typename: "ExecutionStepFailureEvent", message: "KMA_COLLECTION_FAILED",
    error: { className: "Failure", message: `https://example.invalid/?key=${secret}` },
    failureMetadata: { metadataEntries: [
      { label: "stage", text: "publish" }, { label: "fetch_ms", floatValue: 122.5 },
      { label: "normalize_ms", floatValue: 0 }, { label: "publish_ms", floatValue: 8.25 },
      { label: "published_values", intValue: 200 }, { label: "completed_grids", intValue: 2 },
    ] },
  }] } }] } } }));
  expect(result).toContain("오류 코드: KMA_COLLECTION_FAILED");
  expect(result).toContain("실패 단계: DB 게시");
  expect(result).toContain("요청 소요(ms): 122.5");
  expect(result).toContain("정규화 소요(ms): 0");
  expect(result).toContain("게시 소요(ms): 8.25");
  expect(result).toContain("저장 값 수: 200");
  expect(result).toContain("완료 격자 수: 2");
  expect(result).toContain("호출 보호 후 다음 정기 실행");
  expect(result).not.toContain("UNKNOWN_FAILURE");
  expect(result).not.toContain(secret);
  expect(result).not.toContain("https://");
});

it("모든 값 건너뜀은 일반 실패로 숨기지 않고 안전한 건수를 표시한다", () => {
  const result = failureDiagnostic({ message: "KMA 값을 모두 건너뛰어 수집에 실패했습니다.",
    failureMetadata: { metadataEntries: [
      { label: "error_code", text: "KMA_ALL_VALUES_SKIPPED" },
      { label: "values_attempted", intValue: 10 }, { label: "values_skipped", intValue: 10 },
      { label: "missing_values", intValue: 8 }, { label: "invalid_values", intValue: 2 },
    ] },
  });
  expect(result).toContain("오류 코드: KMA_ALL_VALUES_SKIPPED");
  expect(result).toContain("시도 값 수: 10");
  expect(result).toContain("건너뛴 값 수: 10");
  expect(result).toContain("결측 값 수: 8");
  expect(result).toContain("범위 밖 값 수: 2");
  expect(result).toContain("빈 결과로 마지막 정상 자료를 바꾸지 않습니다");
});
