import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/locations" }));

import { AdminShell } from "./admin-shell";

describe("Weather 공통 AdminShell 연결", () => {
  it("공통 셸의 한 상단 바와 기존 정본 메뉴·본문을 함께 렌더한다", () => {
    const html = renderToStaticMarkup(createElement(AdminShell, {
      title: "위치 카탈로그", description: "활성 2곳 · 승인 위치 조회",
      children: createElement("p", null, "시험 본문"),
    }));
    expect(html.match(/data-slot="admin-shell-header"/g)).toHaveLength(1);
    expect(html.match(/<h1/g)).toHaveLength(1);
    expect(html).toContain('data-slot="admin-shell-menu-button"');
    expect(html).toContain('data-slot="admin-shell-description" title="활성 2곳 · 승인 위치 조회"');
    expect(html).toContain('aria-current="page"');
    for (const href of ["/", "/weather", "/locations", "/datasets", "/sync-runs", "/admin/dagster", "/settings/providers", "/api-test"]) {
      expect(html).toContain(`href="${href}"`);
    }
    expect(html).toContain('id="main-content"');
    expect(html).toContain("시험 본문");
    expect(html).toContain("로그아웃");
  });

  it("공통 상단 바에 도움말과 페이지 action을 보존한다", () => {
    const html = renderToStaticMarkup(createElement(AdminShell, {
      title: "위치 카탈로그", help: "위치 도움말",
      actions: createElement("button", { type: "button" }, "목록 새로고침"), children: "본문",
    }));
    expect(html).toContain('aria-label="도움말: 위치 카탈로그"');
    expect(html).toContain("목록 새로고침");
    expect(html.match(/data-slot="admin-shell-header"/g)).toHaveLength(1);
  });
});
