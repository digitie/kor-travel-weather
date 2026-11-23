import { readFileSync } from "node:fs";
import ts from "typescript";
import { expect, test, type Locator, type Page } from "@playwright/test";

// 공용 AdminShell(@kor-travel/ui 0.2.5) 채택 UI 시험. 메모리 fixture API만 쓰며 운영 API·타일과 무관하다.
const ADMIN_PAGES = [
  ["/", "운영 홈"],
  ["/weather", "날씨 지도"],
  ["/locations", "위치 카탈로그"],
  ["/datasets", "데이터셋"],
  ["/sync-runs", "수집 실행"],
  ["/admin/dagster", "Dagster 운영"],
  ["/settings/providers", "제공처 API 키"],
  ["/api-test", "API 테스트"],
] as const;

const SIDO = ["서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시", "경기도", "강원특별자치도", "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도", "경상남도", "제주특별자치도"];

async function routeWeather(page: Page) {
  const stamp = new Date().toISOString();
  const locations = SIDO.map((sido, index) => ({ location_id: `shell-${index}`, name: `${sido} 시험 위치`, latitude: 34 + (index % 8) * 0.5, longitude: 126.5 + Math.floor(index / 8) * 1.5 + (index % 3) * 0.3, nx: null, ny: null, region_code: null, sido_name: sido, sigungu_name: null, enabled: true, metadata: {} }));
  const value = { value_id: "temperature", provider: "python-kma-api", dataset_key: "nowcast", weather_domain: "weather", forecast_style: "nowcast", metric_key: "T1H", metric_name: "기온", value_number: 20, value_text: null, unit: "°C", target_at: stamp, collected_at: stamp, source_record_key: "shell-fixture" };
  await page.route("**/api/weather/**", async (route) => {
    const url = new URL(route.request().url());
    const data = url.pathname.endsWith("/providers") ? [{ provider: "python-kma-api", label: "기상청", datasets: [{ key: "nowcast", label: "초단기실황" }] }]
      : url.pathname.endsWith("/locations") ? locations
      : url.pathname.endsWith("/markers") ? url.searchParams.getAll("location_id").map((id) => ({ location_id: id, latest: [{ ...value, location_id: id }], forecast: [], alerts: [], measurement_point: null }))
      : url.pathname.endsWith("/latest") ? [{ ...value, location_id: url.pathname.split("/").at(-2) }]
      : [];
    await route.fulfill({ json: { data, meta: {} } });
  });
}

async function login(page: Page, destination: string) {
  await page.goto(`/login?next=${destination}`);
  await page.locator('input[name="username"]').fill("admin");
  await page.locator('input[name="password"]').fill(process.env.E2E_UI_PASSWORD!);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  // `/login?next=/`도 "/"로 끝나므로 경로를 정확히 비교한다.
  await expect.poll(() => new URL(page.url()).pathname, { timeout: 30_000 }).toBe(destination);
}

async function tabTo(page: Page, target: Locator) {
  await page.mouse.click(2, page.viewportSize()!.height - 2);
  for (let index = 0; index < 60; index += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((element) => element === document.activeElement)) return;
  }
  throw new Error("Tab으로 대상에 도달하지 못했다");
}

async function chrome(target: Locator) {
  return target.evaluate((element) => {
    const probe = document.createElement("span");
    probe.style.outlineColor = "var(--kt-focus)";
    document.body.append(probe);
    const focus = getComputedStyle(probe).outlineColor;
    probe.remove();
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return { focus, focusVisible: element.matches(":focus-visible"), outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth, outlineOffset: style.outlineOffset, outlineColor: style.outlineColor, borderWidth: style.borderTopWidth, borderColor: style.borderTopColor, boxShadow: style.boxShadow, transform: style.transform, y: box.y };
  });
}

for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) test.describe(`${viewport.width}px 관리자 셸`, () => {
  test.use({ viewport, hasTouch: viewport.width < 1024 });

  test("모든 관리자 화면의 한 줄 제목과 작은 설명", async ({ page }) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    // 화면 순회는 fixture 서버의 빈 응답으로 본다(날씨 전용 route는 위치 관리 화면 응답 모양과 다르다).
    // 주소 갱신 요약은 빈 응답 모양이 맞지 않으므로 조회 실패로 돌려 화면의 오류 안내 경로를 탄다.
    await page.route("**/api/address-refresh", (route) => route.fulfill({ status: 503, json: { detail: "fixture" } }));
    await login(page, "/");
    for (const [path, label] of ADMIN_PAGES) {
      await page.goto(path);
      const topBar = page.locator('[data-slot="admin-shell-header"]');
      await expect(topBar.getByRole("heading", { level: 1 })).toHaveText(`날씨 - ${label}`);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      // 앱 설명 워드마크("Weather Scraper")는 없다.
      await expect(page.getByText(/Weather Scraper/i)).toHaveCount(0);
      const description = topBar.locator('[data-slot="admin-shell-description"]');
      await expect(description).toHaveCount(1);
      const size = await description.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize));
      expect(size).toBeLessThanOrEqual(12);
      expect((await topBar.boundingBox())!.height).toBeLessThanOrEqual(56);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    await routeWeather(page);
    await page.goto("/weather");
    await expect(page.locator('[data-slot="admin-shell-description"]')).toHaveText(`활성 위치 카탈로그 ${SIDO.length}곳`);
    if (viewport.width >= 1024) {
      await expect(page.locator('[data-slot="admin-shell-rail"]')).toBeVisible();
      await expect(page.locator('[data-slot="admin-shell-menu-button"]')).toBeHidden();
      await expect(page.getByTestId("weather-common-menu").getByRole("link", { name: "날씨 지도" })).toHaveAttribute("aria-current", "page");
    }
  });

  test("Tab 포커스는 outline 2px 하나이고 누르면 테두리가 굵어지지 않는다", async ({ page }) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    await login(page, "/settings/providers");
    const button = page.locator('[data-slot="admin-shell-actions"]').getByRole("button", { name: "새로고침" });
    await expect(button).toBeVisible();
    await page.mouse.move(1, viewport.height - 1);
    const rest = await chrome(button);
    await tabTo(page, button);
    const focus = await chrome(button);
    expect(focus.focusVisible).toBe(true);
    expect([focus.outlineStyle, focus.outlineWidth, focus.outlineOffset, focus.outlineColor]).toEqual(["solid", "2px", "2px", focus.focus]);
    // 테두리는 쉬는 상태와 같고(색·폭), ring(box-shadow)은 겹치지 않는다.
    expect([focus.borderWidth, focus.borderColor]).toEqual([rest.borderWidth, rest.borderColor]);
    expect(focus.boxShadow).toBe(rest.boxShadow);
    const box = (await button.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    const pressed = await chrome(button);
    await page.mouse.up();
    expect([pressed.borderWidth, pressed.borderColor]).toEqual([rest.borderWidth, rest.borderColor]);
    // 공용 Button의 눌림 이동(translate-y-px, CSS `translate`, 전환 중) 1px까지만 허용한다. 전역 transform이 겹치면 2px가 된다.
    expect(pressed.transform).toBe("none");
    expect(Math.abs(pressed.y - rest.y)).toBeLessThanOrEqual(1);
    if (viewport.width < 1024) {
      const menu = page.locator('[data-slot="admin-shell-menu-button"]');
      await tabTo(page, menu);
      const menuFocus = await chrome(menu);
      expect([menuFocus.outlineStyle, menuFocus.outlineWidth, menuFocus.outlineOffset, menuFocus.outlineColor]).toEqual(["solid", "2px", "2px", menuFocus.focus]);
    }
  });

  test("지역 날씨 모두 보기 버튼과 목록이 없고 지도 마커는 키보드로 고른다", async ({ page }) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    await routeWeather(page);
    await login(page, "/weather");
    // 좁은 화면에서는 첫 위치 상세가 지도 대부분을 덮어 카드 자리가 없다. 닫고 본다.
    const detail = page.getByRole("complementary", { name: "선택한 위치 상세" });
    if (await detail.isVisible()) await detail.getByRole("button", { name: "상세 닫기", exact: true }).click();
    const placed = page.locator('.weather-region-marker:not([aria-hidden="true"]) .vworld-weather-marker-button');
    await expect(placed.first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: /지역 날씨/ })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "모든 지역 대표 날씨" })).toHaveCount(0);
    await expect(page.locator(".weather-region-access, .weather-region-access-list")).toHaveCount(0);
    // 자리가 없어 숨긴 카드는 키보드에서도 빠진다.
    for (const hidden of await page.locator('.weather-region-marker[aria-hidden="true"] .vworld-weather-marker-button').all()) {
      await expect(hidden).toHaveAttribute("tabindex", "-1");
    }
    const marker = placed.first();
    await expect(marker).toHaveAttribute("tabindex", "0");
    await marker.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("complementary", { name: "선택한 위치 상세" })).toBeVisible();
  });
});

test.describe("390px 메뉴 drawer", () => {
  // 위치 측정이 열림 애니메이션 시점에 좌우되지 않게 한다.
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, contextOptions: { reducedMotion: "reduce" } });
  test("좌측 drawer로 메뉴를 열고 이동하면 닫힌다", async ({ page }) => {
    test.skip(!process.env.E2E_UI_PASSWORD, "전용 로컬 UI 암호 필요");
    await routeWeather(page);
    await login(page, "/weather");
    await expect(page.locator('[data-slot="admin-shell-rail"]')).toBeHidden();
    const menuButton = page.locator('[data-slot="admin-shell-menu-button"]');
    await expect(menuButton).toBeVisible();
    // 열기 전에는 상단 바 한 줄 외에 메뉴가 화면을 차지하지 않는다.
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await menuButton.click();
    const drawer = page.getByRole("dialog", { name: "메뉴" });
    await expect(drawer).toBeVisible();
    const drawerBox = (await drawer.boundingBox())!;
    expect(drawerBox.x).toBe(0);
    expect(drawerBox.width).toBeLessThanOrEqual(280);
    await expect(drawer.getByRole("link", { name: "날씨 지도" })).toHaveAttribute("aria-current", "page");
    await expect(drawer.getByRole("button", { name: "로그아웃" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(menuButton).toBeFocused();
    await menuButton.click();
    await page.getByRole("dialog", { name: "메뉴" }).getByRole("link", { name: "수집 실행" }).click();
    await expect(page).toHaveURL(/\/sync-runs$/);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("날씨 - 수집 실행");
  });
});

// 복사한 포커스 분기 대신 실제 배치 effect/배치 함수와 앱 CSS를 Chromium에서 실행한다.
const markerSource = readFileSync("components/weather-map.tsx", "utf8");
const markerTree = ts.createSourceFile("weather-map.tsx", markerSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let positionEffect: ts.Expression | undefined;
function findPositionEffect(node: ts.Node) {
  if (ts.isCallExpression(node) && node.expression.getText(markerTree) === "useEffect"
    && node.arguments[0]?.getText(markerTree).includes("const offsets = layoutWeatherRegionCards")) positionEffect = node.arguments[0];
  ts.forEachChild(node, findPositionEffect);
}
findPositionEffect(markerTree);
const layoutNode = markerTree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === "layoutWeatherRegionCards");
if (!positionEffect || !layoutNode) throw new Error("실제 지역 카드 배치 effect와 함수가 필요합니다.");
const positionCode = ts.transpileModule("const effect = " + positionEffect.getText(markerTree), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const layoutCode = ts.transpileModule(layoutNode.getText(markerTree).replace("export ", ""), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const overflowCSS = readFileSync("app/globals.css", "utf8")
  .match(/\.weather-region-marker\[data-region-overflow="true"\] \.vworld-weather-marker-button\s*\{[^}]+\}/)?.[0];
if (!overflowCSS) throw new Error("실제 숨김 CSS가 필요합니다.");

for (const cleanupBetween of [false, true]) for (const userMoved of [false, true]) test("390px 실제 지역 배치가 숨긴 포커스: " + (cleanupBetween ? "다음 frame 전 effect cleanup · " : "") + (userMoved ? "사용자의 다른 입력 유지" : "브라우저 BODY blur 복구"), async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.setContent('<style>#map {position:relative;width:200px;height:100px}.weather-region-marker {position:absolute;left:20px;top:20px}.vworld-weather-marker-button {width:150px;height:50px}' + overflowCSS + '</style><div id="map"><div class="weather-region-marker"><button id="visible" class="vworld-weather-marker-button">보이는 지역</button></div><div id="hidden-host" class="weather-region-marker"><button id="hidden" class="vworld-weather-marker-button">공간 부족 지역</button></div><canvas tabindex="0"></canvas></div><input id="user" aria-label="다른 입력"><output id="selected"></output>');
  const result = await page.evaluate(async ({ positionCode, layoutCode, userMoved, cleanupBetween }) => {
    const root = document.querySelector<HTMLElement>("#map")!;
    const host = document.querySelector<HTMLElement>("#hidden-host")!;
    const button = document.querySelector<HTMLButtonElement>("#hidden")!;
    const user = document.querySelector<HTMLInputElement>("#user")!;
    document.querySelector("#visible")!.addEventListener("click", () => {
      document.querySelector("#selected")!.textContent = "선택됨";
    });
    let browserBlurred = false;
    const setAttribute = host.setAttribute.bind(host);
    host.setAttribute = (name, value) => {
      setAttribute(name, value);
      if (name !== "aria-hidden" || value !== "true") return;
      // 실제 visibility 변경의 스타일 계산을 완료시킨다. focus/contains/document는 모의하지 않는다.
      button.getBoundingClientRect();
      browserBlurred ||= document.activeElement === document.body;
      if (userMoved) queueMicrotask(() => user.focus());
    };
    button.focus();
    const hadFocus = document.activeElement === button;
    const startEffect = () => new Function("map", "mapReady", "mode", "regionLevel", "markers",
      layoutCode + "\n" + positionCode + "\nreturn effect();")({
        current: { getContainer: () => root, getCanvas: () => root.querySelector("canvas"), on() {}, off() {} },
      }, true, "map", "sido", [1, 2]) as () => void;
    let cleanup = startEffect();
    try {
      // 같은 첫 frame에서 position 다음에 React cleanup→새 effect가 올 수 있다.
      await new Promise<void>(resolve => requestAnimationFrame(() => {
        if (cleanupBetween) {
          cleanup();
          cleanup = startEffect();
        }
        requestAnimationFrame(() => resolve());
      }));
      return { hadFocus, browserBlurred, active: document.activeElement?.id, overflow: host.dataset.regionOverflow, tabIndex: button.tabIndex };
    } finally { cleanup(); }
  }, { positionCode, layoutCode, userMoved, cleanupBetween });
  expect(result).toEqual({ hadFocus: true, browserBlurred: true, active: userMoved ? "user" : "visible", overflow: "true", tabIndex: -1 });
  if (!userMoved) {
    await page.keyboard.press("Enter");
    await expect(page.locator("#selected")).toHaveText("선택됨");
  }
});
