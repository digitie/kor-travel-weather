import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";

import { LoginForm } from "./LoginForm";

// 공용 UI dev.9: 서버 렌더와 최초 hydration 동안 입력·제출은 native disabled다.
// client handler가 준비되기 전의 클릭·Enter가 기본 POST /login으로 새지 않게 한다.
// 활성화(effect)는 브라우저에서만 일어나므로 여기서는 Weather 래퍼의 SSR 결과만 고정한다.
function slotTag(html: string, slot: string): string {
  return html.match(new RegExp(`<(?:input|button)[^>]*data-slot="${slot}"[^>]*>`))?.[0] ?? "";
}

test("Weather 로그인 화면의 서버 렌더는 hydration 전까지 입력과 제출을 닫는다", () => {
  const html = renderToStaticMarkup(createElement(LoginForm, { nextPath: "/weather" }));
  for (const slot of ["login-username", "login-password", "login-submit"]) {
    expect(slotTag(html, slot), slot).toMatch(/\sdisabled=""/);
  }
  // 기본 아이디와 이름·자동완성 계약은 그대로다.
  expect(slotTag(html, "login-username")).toContain('value="admin"');
  expect(slotTag(html, "login-username")).toContain('name="username"');
  expect(slotTag(html, "login-password")).toMatch(/autocomplete="current-password"/i);
});
