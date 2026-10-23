// Map 서버간 API의 타입 계약이다. 브라우저 호출 함수·서비스 토큰은 제공하지 않는다.
export type ExportMeta = {
  generated_at: string;
  source_collected_at: string | null;
  source_status: "ok" | "stale" | "failed";
  item_count: number;
  snapshot_id?: string | null;
  snapshot_generated_at?: string | null;
  as_of?: string | null;
  next_cursor?: string | null;
  has_more?: boolean;
};

export type ExportLocation = {
  location_id: string;
  name: string;
  latitude: number | null;
  longitude: number | null;
  legal_dong_code?: string | null;
  sido_name?: string | null;
  sigungu_name?: string | null;
};

export type ExportCurrentValue = {
  location_id: string;
  provider: string;
  metric_key: string;
  value: number | string | null;
  unit: string | null;
  target_at: string;
  known_at: string;
  forecast_style: "observed" | "nowcast";
};

export type ExportSourceStatus = {
  provider: string;
  forecast_style: "observed" | "nowcast" | "ultra_short" | "short" | "mid" | "external";
  last_success_at: string | null;
  last_failed_at: string | null;
  last_attempt_failed: boolean;
  next_scheduled_at: string | null;
};

export type ExportPage<T> = { items: T[]; meta: ExportMeta };

export type ExportUnavailable = {
  type?: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  request_id: string;
  errors?: Record<string, unknown>[];
  meta: ExportMeta;
};

// 고정 snapshot cursor는 resource/limit에 묶이며 만료 뒤 첫 페이지부터 재시작한다.
export type ExportCursorError = ExportUnavailable & {
  status: 410 | 422;
  code: "EXPORT_SNAPSHOT_EXPIRED" | "EXPORT_CURSOR_INVALID";
};
