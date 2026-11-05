/** 공식 발표 관측이다. 동일 경보 회차 연결과 빈 목록의 해제 의미를 보장하지 않는다. */
export type RegionalAlertEvent = {
  event_id: string;
  announcement_id: string;
  area_code: string;
  area_name: string;
  warn_var: string;
  warning_name: string | null;
  warn_stress: string;
  stress_name: string | null;
  command: string;
  cancel: string;
  action: "issued" | "released" | "extended" | "corrected" | "changed_issue" | "changed_release" | "cancelled" | "unknown";
  issued_at: string | null;
  starts_at: string | null;
  ends_at: string | null;
  all_ends_at: string | null;
  occurrence_id: null;
  correlation_status: "unresolved";
  normalization_version: string;
  first_seen_at: string;
  source_record_key: string;
  scope_status: "verified";
  bjd_prefixes: string[];
};

export type RegionalAlertEvents = {
  namespace: "kr_bjd";
  bjd_code: string;
  region_status: "known" | "unknown";
  mapping_version: string;
  mapping_valid_from: string;
  mapping_coverage: "partial";
  unverified_scope_count: number;
  collection_status: string;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_error_code: string | null;
  fresh: boolean;
  events_complete: boolean;
  retained_since: string;
  history_available_since: string | null;
  queried_since: string;
  absence_is_clear: false;
  events: RegionalAlertEvent[];
};
