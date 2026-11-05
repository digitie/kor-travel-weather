export const REGIONAL_ALERT_DATASET = "kma_regional_alert_events";
export const REGIONAL_ALERT_LABEL = "지역별 기상특보 발표 관측";

const reasons: Record<string, string> = {
  ALERT_PAGE_METADATA: "페이지 번호·페이지 크기·전체 행 수가 원천 응답 규격과 일치하지 않습니다.",
  ALERT_PAGE_ROWS: "선언된 행 수에 비해 페이지의 실제 반환 행 수가 다릅니다.",
  ALERT_PAGE_TOTAL_CHANGED: "페이지를 조회하는 동안 전체 행 수가 바뀌어 이번 결과를 게시하지 않았습니다.",
  ALERT_PAGE_DUPLICATE: "같은 발표 관측이 여러 페이지에 반복되어 결과를 확정하지 못했습니다.",
  ALERT_TOO_MANY_ROWS: "원천 결과가 최대 10,000행·100페이지 상한을 넘었습니다.",
  ALERT_RESPONSE_TOO_LARGE: "원천 응답이 8MiB 크기 상한을 넘었습니다.",
  ALERT_FIRST_PAGE_CHANGED: "전체 조회 뒤 첫 페이지가 바뀌어 이번 결과를 게시하지 않았습니다.",
  ALERT_RUN_OWNERSHIP_LOST: "실행 소유권이 회수되어 뒤늦은 결과 게시를 막았습니다.",
  ALERT_TIMEOUT: "기상특보 요청 또는 수집이 허용된 시간을 넘었습니다.",
  ALERT_PROVIDER_AUTH: "기상청 서비스 키의 인증 또는 접근 권한이 거절되었습니다.",
  ALERT_PROVIDER_RATE_LIMIT: "기상청 API 요청 한도에 도달했습니다.",
  ALERT_PROVIDER_UNAVAILABLE: "기상청 요청에 실패했습니다. 네트워크와 제공자 상태를 확인하세요.",
  ALERT_COLLECTION_CANCELLED: "기상특보 수집을 취소했습니다.",
  ALERT_COLLECTION_FAILED: "기상특보 수집을 완료하지 못했습니다.",
};

export function syncRunErrorText(dataset: string, error: string | null): string | null {
  if (!error || dataset !== REGIONAL_ALERT_DATASET) return error;
  const reason = Object.hasOwn(reasons, error) ? reasons[error] : undefined;
  return reason
    ? reason + " (" + error + ")"
    : "기상특보 수집 실패의 원인을 확인할 수 없습니다.";
}
