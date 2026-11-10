import { describe, expect, it } from "vitest";

import type { SyncRun } from "./api";
import { type HomeFetchers, type HomeSummary, loadingSummary, startHomeSummary } from "./home-summary";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function harness() {
  const active = deferred<number>();
  const catalog = deferred<number>();
  const runs = deferred<SyncRun[]>();
  const health = deferred<string>();
  const fetchers: HomeFetchers = {
    active: () => active.promise,
    catalog: () => catalog.promise,
    runs: () => runs.promise,
    health: () => health.promise,
  };
  let summary: HomeSummary = loadingSummary();
  const cancel = startHomeSummary(fetchers, (card, state) => {
    summary = { ...summary, [card]: state };
  });
  return { active, catalog, runs, health, cancel, current: () => summary };
}

describe("home summary cards", () => {
  it("draws a fast card while a slow request is still pending", async () => {
    const h = harness();
    h.health.resolve("정상");
    h.active.resolve(1451);
    await flush();
    expect(h.current().health).toEqual({ status: "ready", value: "정상" });
    expect(h.current().active).toEqual({ status: "ready", value: 1451 });
    expect(h.current().runs).toEqual({ status: "loading" });
    expect(h.current().catalog).toEqual({ status: "loading" });
  });

  it("keeps the other cards when one request fails", async () => {
    const h = harness();
    h.runs.reject(new Error("sync-runs 503"));
    h.health.resolve("정상");
    h.catalog.resolve(3000);
    h.active.resolve(1451);
    await flush();
    expect(h.current().runs).toEqual({ status: "error", message: "sync-runs 503" });
    expect(h.current().catalog).toEqual({ status: "ready", value: 3000 });
    expect(h.current().health).toEqual({ status: "ready", value: "정상" });
  });

  it("ignores responses that arrive after a newer refresh cancelled this one", async () => {
    const h = harness();
    h.cancel();
    h.health.resolve("정상");
    h.runs.reject(new Error("late"));
    await flush();
    expect(h.current()).toEqual(loadingSummary());
  });

  it("keeps only the latest refresh when an older one resolves late", async () => {
    let summary: HomeSummary = loadingSummary();
    const record = <K extends keyof HomeSummary>(card: K, state: HomeSummary[K]) => {
      summary = { ...summary, [card]: state };
    };
    const older = deferred<number>();
    const newer = deferred<number>();
    const fetchers = (active: Promise<number>): HomeFetchers => ({
      active: () => active,
      catalog: () => new Promise(() => {}),
      runs: () => new Promise(() => {}),
      health: () => new Promise(() => {}),
    });
    const cancelOlder = startHomeSummary(fetchers(older.promise), record);
    cancelOlder();
    summary = loadingSummary();
    startHomeSummary(fetchers(newer.promise), record);
    newer.resolve(2);
    older.resolve(1);
    await flush();
    expect(summary.active).toEqual({ status: "ready", value: 2 });
  });

  it("uses a fallback message for a rejection without an Error message", async () => {
    let summary: HomeSummary = loadingSummary();
    startHomeSummary(
      {
        active: () => Promise.reject("plain string"),
        catalog: () => Promise.reject(new Error("")),
        runs: () => Promise.resolve([]),
        health: () => Promise.resolve("정상"),
      },
      (card, state) => {
        summary = { ...summary, [card]: state };
      },
    );
    await flush();
    expect(summary.active).toEqual({ status: "error", message: "상태를 불러오지 못했습니다." });
    expect(summary.catalog).toEqual({ status: "error", message: "상태를 불러오지 못했습니다." });
  });

  it("turns a synchronously throwing fetcher into that card's error only", async () => {
    let summary: HomeSummary = loadingSummary();
    startHomeSummary(
      {
        active: () => Promise.resolve(1),
        catalog: () => {
          throw new Error("boom");
        },
        runs: () => Promise.resolve([]),
        health: () => Promise.resolve("정상"),
      },
      (card, state) => {
        summary = { ...summary, [card]: state };
      },
    );
    await flush();
    expect(summary.catalog).toEqual({ status: "error", message: "boom" });
    expect(summary.runs).toEqual({ status: "ready", value: [] });
  });
});
