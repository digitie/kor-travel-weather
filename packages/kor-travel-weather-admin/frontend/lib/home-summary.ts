import type { SyncRun } from "./api";

/** 홈 카드 하나의 상태. 카드마다 따로 끝나고 따로 실패한다. */
export type CardState<T> =
  | { status: "loading" }
  | { status: "ready"; value: T }
  | { status: "error"; message: string };

export type HomeSummary = {
  active: CardState<number>;
  catalog: CardState<number>;
  runs: CardState<SyncRun[]>;
  health: CardState<string>;
};

export type HomeFetchers = { [K in keyof HomeSummary]: () => Promise<HomeValue<K>> };

type HomeValue<K extends keyof HomeSummary> = HomeSummary[K] extends CardState<infer T> ? T : never;

export const HOME_CARDS = ["active", "catalog", "runs", "health"] as const satisfies readonly (keyof HomeSummary)[];

export function loadingSummary(): HomeSummary {
  return { active: { status: "loading" }, catalog: { status: "loading" }, runs: { status: "loading" }, health: { status: "loading" } };
}

function failure(reason: unknown): string {
  return reason instanceof Error && reason.message ? reason.message : "상태를 불러오지 못했습니다.";
}

/**
 * 네 요청을 동시에 시작하고, 끝나는 카드부터 하나씩 알린다.
 *
 * 예전 홈은 Promise.all로 넷을 모두 기다린 뒤 한 번에 그렸다. 느린 요청 하나가 빠른 카드까지
 * 붙잡았고, 실패 하나가 나머지 성공 값을 모두 버렸다. 돌려받은 cancel을 부르면 그 뒤에 끝나는
 * 응답은 무시된다(새로고침이 겹쳐도 마지막 요청만 화면에 남는다).
 */
export function startHomeSummary(
  fetchers: HomeFetchers,
  onChange: <K extends keyof HomeSummary>(card: K, state: HomeSummary[K]) => void,
): () => void {
  let cancelled = false;
  for (const card of HOME_CARDS) {
    const settle = (state: HomeSummary[typeof card]) => {
      if (!cancelled) onChange(card, state);
    };
    let pending: Promise<unknown>;
    try {
      pending = fetchers[card]();
    } catch (reason: unknown) {
      pending = Promise.reject(reason);
    }
    pending.then(
      (value) => settle({ status: "ready", value } as HomeSummary[typeof card]),
      (reason: unknown) => settle({ status: "error", message: failure(reason) }),
    );
  }
  return () => {
    cancelled = true;
  };
}
