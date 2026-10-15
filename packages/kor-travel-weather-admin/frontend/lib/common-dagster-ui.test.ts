import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DagsterOperations, type DagsterSnapshot } from "@kor-travel/ui";
import { expect, test } from "vitest";

test("설치한 공통 UI는 실행 검색과 제한된 50행 페이지를 제공한다", () => {
  const snapshot: DagsterSnapshot = {
    checkedAt: "2026-10-15T00:00:00Z",
    repositories: [],
    runs: Array.from({ length: 51 }, (_, index) => ({
      runId: `vendor-run-${index}`,
      jobName: `vendor-job-${index}`,
      status: "SUCCESS",
      startTime: 1,
      endTime: 2,
      errorMessage: null,
    })),
  };
  const html = renderToStaticMarkup(createElement(DagsterOperations, {
    snapshot, onRefresh: () => undefined,
    jobLabel: name => name,
    runUrl: id => `/runs/${id}`,
    scheduleUrl: name => `/schedules/${name}`,
  }));
  expect(html).toContain('aria-label="실행 검색"');
  expect(html).toContain("조회 51건 · 검색 51건 · 1/2 페이지");
  expect(html).toContain("다음 실행 페이지");
  expect(html.match(/href="\/runs\/vendor-run-/g)).toHaveLength(50);
  expect(html).toContain('title="vendor-job-49"');
  expect(html).not.toContain('title="vendor-job-50"');
});
