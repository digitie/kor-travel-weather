/**
 * 관리 지도 마커 요청 큐(호출 검토 B6·B12).
 *
 * - 화면 범위의 위치 ID를 {@link MARKER_BATCH_SIZE}개 묶음으로 최대 {@link MARKER_BATCH_CONCURRENCY}개
 *   동시에 보낸다. 진행 중인 묶음은 지도가 움직여도 끊지 않고, 다음 배정만 새 범위로 좁힌다.
 * - 실패한 위치는 영구히 막지 않는다. 최종 실패(요청 함수의 재시도까지 끝난 실패)와 응답 누락을
 *   나눠 지수 backoff 뒤 화면에 남아 있으면 자동으로 다시 보낸다. 수동 "다시 조회"는 대기를 지운다.
 * - `fresh`는 이번 세대(새로고침·필터 범위)에 받은 ID다. 새로고침은 새 `fresh`로 시작하므로
 *   이미 그린 마커(`cache`)를 지우지 않고 다시 받아 바꾼다.
 */

export const MARKER_BATCH_SIZE = 60;
export const MARKER_BATCH_CONCURRENCY = 2;
/** 묶음 요청이 최종 실패한 위치의 첫 자동 재시도 대기(ms). 시도마다 두 배. */
export const MARKER_ERROR_RETRY_MS = 5_000;
/** 응답에 빠진 위치(비활성·삭제 등)의 첫 자동 재시도 대기(ms). 시도마다 두 배. */
export const MARKER_MISSING_RETRY_MS = 60_000;
/** 자동 재시도 대기 상한(ms). */
export const MARKER_RETRY_MAX_MS = 5 * 60_000;
export const MARKER_PARTIAL_ERROR = "일부 위치의 날씨를 불러오지 못했습니다. 잠시 후 자동으로 다시 시도합니다.";

type Failure = { attempts: number; until: number };

export type MarkerQueueOptions<T extends { location_id: string }> = {
  fetchBatch: (ids: string[], signal: AbortSignal) => Promise<{ data: T[] }>;
  /** 화면이 그리는 마커 값. 새로고침에도 유지하고 받은 항목만 바꾼다. */
  cache: { current: Record<string, T> };
  /** 이번 세대에 이미 받은 ID. 같은 세대의 새 큐(지도↔목록 전환)는 다시 받지 않는다. */
  fresh: Set<string>;
  onData: (cache: Record<string, T>) => void;
  onError: (message: string) => void;
  now?: () => number;
};

export type MarkerQueue = {
  /** 화면 범위의 ID(가까운 순). `retryToken`이 바뀌면 그 ID의 재시도 대기를 지운다. */
  update: (ids: string[], retryToken: number) => void;
  cancel: () => void;
};

/** `retryAt` 기준 다음 자동 재시도 대기(ms). */
export function markerRetryDelay(attempts: number, missing: boolean): number {
  const base = missing ? MARKER_MISSING_RETRY_MS : MARKER_ERROR_RETRY_MS;
  return Math.min(MARKER_RETRY_MAX_MS, base * 2 ** Math.max(0, attempts - 1));
}

export function createMarkerQueue<T extends { location_id: string }>(
  options: MarkerQueueOptions<T>,
): MarkerQueue {
  const now = options.now ?? Date.now;
  const controller = new AbortController();
  const pending = new Set<string>();
  const failed = new Map<string, Failure>();
  let ids: string[] = [];
  let active = 0;
  let cancelled = false;
  let retryToken: number | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let timerAt = Infinity;

  const fail = (id: string, missing: boolean) => {
    const attempts = (failed.get(id)?.attempts ?? 0) + 1;
    failed.set(id, { attempts, until: now() + markerRetryDelay(attempts, missing) });
  };

  const schedule = () => {
    // 화면에 남은 실패 위치 중 가장 이른 재시도 시각에 한 번 깨운다.
    let earliest = Infinity;
    for (const id of ids) {
      const failure = failed.get(id);
      if (failure && !pending.has(id)) earliest = Math.min(earliest, failure.until);
    }
    if (earliest === Infinity || earliest >= timerAt) return;
    clearTimeout(timer);
    timerAt = earliest;
    timer = setTimeout(() => {
      timer = undefined;
      timerAt = Infinity;
      pump();
    }, Math.max(0, earliest - now()));
  };

  const pump = () => {
    if (cancelled) return;
    options.onError(ids.some((id) => failed.has(id)) ? MARKER_PARTIAL_ERROR : "");
    const current = now();
    while (active < MARKER_BATCH_CONCURRENCY) {
      const chunk: string[] = [];
      for (const id of ids) {
        if (chunk.length >= MARKER_BATCH_SIZE) break;
        if (options.fresh.has(id) || pending.has(id)) continue;
        const failure = failed.get(id);
        if (failure && failure.until > current) continue;
        chunk.push(id);
      }
      if (!chunk.length) break;
      chunk.forEach((id) => pending.add(id));
      active += 1;
      void options.fetchBatch(chunk, controller.signal)
        .then((response) => {
          if (cancelled) return;
          const returned = new Set<string>();
          for (const item of response.data) {
            options.cache.current[item.location_id] = item;
            options.fresh.add(item.location_id);
            failed.delete(item.location_id);
            returned.add(item.location_id);
          }
          // 빈/부분 응답의 누락 위치는 즉시 다시 보내지 않고 긴 대기 뒤에만 다시 본다.
          for (const id of chunk) if (!returned.has(id)) fail(id, true);
          options.onData(options.cache.current);
        })
        .catch(() => {
          if (!cancelled) chunk.forEach((id) => fail(id, false));
        })
        .finally(() => {
          chunk.forEach((id) => pending.delete(id));
          active -= 1;
          pump();
        });
    }
    schedule();
  };

  return {
    update(next, token) {
      if (cancelled) return;
      ids = next;
      if (retryToken !== token) {
        // 첫 update는 기준만 잡는다. 이후 바뀐 token은 수동 재조회다.
        if (retryToken !== null) for (const id of ids) failed.delete(id);
        retryToken = token;
      }
      pump();
    },
    cancel() {
      cancelled = true;
      clearTimeout(timer);
      controller.abort();
    },
  };
}

type Positioned = { location_id: string; longitude: number; latitude: number };

/**
 * 마커 요청 순서: 화면 중심에서 가까운 위치부터. 중심을 모르면 받은 순서를 유지한다.
 * 한국 범위라 경도 차이에 위도 cos만 곱한 평면 거리로 충분하다.
 */
export function markerFetchOrder(
  locations: readonly Positioned[],
  center: readonly [number, number] | null,
): string[] {
  if (!center) return locations.map((location) => location.location_id);
  const scale = Math.cos((center[1] * Math.PI) / 180);
  return locations
    .map((location, index) => ({
      id: location.location_id,
      index,
      distance: ((location.longitude - center[0]) * scale) ** 2 + (location.latitude - center[1]) ** 2,
    }))
    .sort((left, right) => left.distance - right.distance || left.index - right.index)
    .map((entry) => entry.id);
}
