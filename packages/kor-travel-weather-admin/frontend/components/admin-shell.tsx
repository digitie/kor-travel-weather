"use client";
// Hallmark · genre: editorial-utilitarian · macrostructure: Rail-Workbench · design-system: design.md · designed-as-app

import {
  ActivityIcon,
  CloudSunIcon,
  CodeIcon,
  DatabaseIcon,
  HomeIcon,
  KeyRoundIcon,
  LogOutIcon,
  MapPinIcon,
  WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import { AppMenu, type AppMenuLinkProps } from "@kor-travel/ui/app-menu";
import { AdminShell as CommonAdminShell, type AdminShellSlotState } from "@kor-travel/ui/admin-shell";
import { usePathname } from "next/navigation";
import {
  Fragment,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { withOccurrenceKeys } from "@/lib/occurrence-key";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { HelpTip } from "@/components/help-tip";
import {
  beginAdminLogout,
  cancelPendingAdminLogout,
  publishAdminLogout,
} from "@/lib/admin-auth-events";
import { cn } from "@/lib/utils";

/**
 * 작업 지향 nav 그룹 (§1) — nav·섹션 라벨·브레드크럼 라벨의 단일 정본.
 * 그룹 헤더는 비링크이며 href는 존치하는 canonical 화면만 가리킨다.
 */
const NAV_GROUPS = [
  {
    group: null,
    section: "개요",
    items: [{ href: "/", label: "홈", icon: HomeIcon }],
  },
  {
    group: "날씨",
    section: "날씨",
    items: [
      { href: "/weather", label: "날씨 지도", icon: CloudSunIcon },
      { href: "/locations", label: "위치 카탈로그", icon: MapPinIcon },
      { href: "/datasets", label: "데이터셋", icon: DatabaseIcon },
    ],
  },
  {
    group: "수집 파이프라인",
    section: "수집 파이프라인",
    items: [
      { href: "/sync-runs", label: "수집 실행", icon: ActivityIcon },
      { href: "/admin/dagster", label: "Dagster", icon: WorkflowIcon },
    ],
  },
  {
    group: "설정·도구",
    section: "설정·도구",
    items: [
      { href: "/settings/providers", label: "API 키 설정", icon: KeyRoundIcon },
      { href: "/api-test", label: "API 테스트", icon: CodeIcon },
    ],
  },
] as const;

function MapMenuLink(props: AppMenuLinkProps) {
  return <Link {...props} />;
}

const COMMON_MENU_GROUPS = NAV_GROUPS.map((group) => ({
  id: group.group ?? "root",
  label: group.group ?? undefined,
  items: group.items.map(({ href, label, icon: Icon }) => ({
    id: href,
    href,
    label,
    icon: <Icon aria-hidden="true" className="size-4" />,
  })),
}));

const navItems = NAV_GROUPS.flatMap((group) =>
  group.items.map((item) => ({ ...item, section: group.section })),
);

const SIDEBAR_COLLAPSED_KEY = "kor-travel-weather:sidebar-collapsed";
const MAIN_CONTENT_ID = "main-content";

function isActive(pathname: string, href: string) {
  if (href === "/") {
    return pathname === "/";
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Rail nav row / footer row 레시피 (design.md §Macrostructure N3 · M9).
 * flat row · 30px · 500 · hover paper-2 · pressed rules 색 · focus = 단일 outline 레시피.
 * active 는 색만이 아니라 좌측 2px brand mark + `aria-current="page"`(M11).
 */
const railRowClass =
  "relative flex h-control-sm shrink-0 items-center gap-2.5 rounded-control px-3 text-xs font-medium whitespace-nowrap text-text-secondary transition-[color,background-color] duration-fast ease-out hover:bg-surface-subtle hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus active:bg-surface-muted";

const railRowCollapsedClass =
  "lg:size-control lg:justify-center lg:gap-0 lg:px-0";

export type AdminBreadcrumb = { label: string; href?: string };

export type AdminShellProps = {
  title: string;
  description?: string;
  /** 명시 오버라이드 — 생략 시 NAV_GROUPS longest-prefix로 유도(§1). 공통 상단 바의 영역 이름으로 렌더. */
  section?: string;
  breadcrumbs?: AdminBreadcrumb[];
  help?: ReactNode;
  helpVariant?: "help" | "info";
  /** 지도처럼 작업면을 먼저 보여야 하는 화면의 세로 여백만 줄인다. */
  compact?: boolean;
  /**
   * 본문이 뷰포트의 남은 높이를 채운다(날씨 지도 작업면). header 아래 본문을 세로 flex로 만들어
   * 자식이 `flex-1`로 남은 높이를 받게 한다. 본문이 넘치면 본문 안에서만 스크롤한다.
   */
  fillViewport?: boolean;
  /**
   * 제목 옆 한 줄 메타(상태·갱신 시각·건수 등). 설명과 같은 줄에서 말줄임한다 —
   * 제목을 반복하는 badge 는 넣지 않는다(M30/M31). 구분자는 `·`.
   */
  meta?: ReactNode;
  /** 헤더 밴드 액션 슬롯 — primary ≤ 1 + secondary ≤ 2 (design.md). 나머지 cross-link 는 rail/breadcrumb 로. */
  actions?: ReactNode;
  children: ReactNode;
};

/** 레일과 drawer가 같은 브랜드·로그아웃 구현을 쓴다. 인증 동작은 기존 함수를 유지한다. */
function ShellBrand({ collapsed }: AdminShellSlotState) {
  return (
    <Link aria-label="Weather Scraper Admin UI" href="/" className="truncate font-semibold">
      {collapsed ? "WS" : "Weather Scraper"}
    </Link>
  );
}

function ShellFooter({ collapsed }: AdminShellSlotState) {
  return <ButtonLogout collapsed={collapsed} />;
}

export function AdminShell({
  title, description, section, breadcrumbs, help, helpVariant,
  compact = false, fillViewport = false, meta, actions, children,
}: AdminShellProps) {
  const pathname = usePathname();
  const activeItem = navItems
    .filter((item) => isActive(pathname, item.href))
    .sort((a, b) => b.href.length - a.href.length)[0];
  const summary = meta ? <>{meta}{description ? <> · {description}</> : null}</> : description;
  const summaryTitle = [typeof meta === "string" ? meta : null, description].filter(Boolean).join(" · ");

  return (
    <CommonAdminShell
      area={section ?? activeItem?.section ?? "날씨"}
      title={title}
      description={summary}
      descriptionTitle={summaryTitle || undefined}
      actions={help || actions ? <>
        {help ? <HelpTip label={title} variant={helpVariant}>{help}</HelpTip> : null}
        {actions}
      </> : undefined}
      menu={<AppMenu
        groups={COMMON_MENU_GROUPS}
        activeItemId={activeItem?.href ?? null}
        pathname={pathname}
        label="주요 메뉴"
        linkComponent={MapMenuLink}
        testId="weather-common-menu"
      />}
      brand={ShellBrand}
      railFooter={ShellFooter}
      navigationKey={pathname}
      collapsedStorageKey={SIDEBAR_COLLAPSED_KEY}
      mainId={MAIN_CONTENT_ID}
      fillViewport={fillViewport}
      labels={{ menu: "주요 메뉴", collapseRail: "좌측 메뉴 접기", expandRail: "좌측 메뉴 펼치기" }}
      className="map-common-surface bg-surface-page text-text-primary"
      drawerClassName="map-common-surface"
      mainClassName={cn(
        "min-w-0 flex-1 px-6 focus-visible:outline-0",
        compact ? "py-3" : "py-6",
        fillViewport && "flex min-h-0 flex-col overflow-y-auto",
      )}
    >
      {breadcrumbs?.length ? <Breadcrumb className="mb-2">
        <BreadcrumbList className="text-xs">
          {withOccurrenceKeys(breadcrumbs, (crumb) => JSON.stringify([crumb.href ?? null, crumb.label]))
            .map(({ key, value: crumb }, index) => <Fragment key={key}>
              {index > 0 ? <BreadcrumbSeparator /> : null}
              <BreadcrumbItem>
                {crumb.href ? <BreadcrumbLink href={crumb.href}>{crumb.label}</BreadcrumbLink>
                  : index === breadcrumbs.length - 1 ? <BreadcrumbPage>{crumb.label}</BreadcrumbPage>
                  : <span>{crumb.label}</span>}
              </BreadcrumbItem>
            </Fragment>)}
        </BreadcrumbList>
      </Breadcrumb> : null}
      {children}
    </CommonAdminShell>
  );
}

function ButtonLogout({
  collapsed = false,
  iconOnly = false,
  className,
}: {
  /** lg rail 접힘 — 라벨을 lg 에서만 숨긴다. */
  collapsed?: boolean;
  /** 모든 breakpoint 에서 아이콘만(<lg 상단 행). */
  iconOnly?: boolean;
  className?: string;
}) {
  const labelHidden = iconOnly || collapsed;
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  async function logout() {
    if (inFlight.current || !beginAdminLogout()) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/logout", {
        method: "POST",
        signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) throw new Error("logout failed");
      publishAdminLogout();
      window.location.assign("/login");
    } catch {
      cancelPendingAdminLogout();
      setError("로그아웃하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return (
    <div>
      <button
        aria-busy={busy}
        aria-disabled={busy}
        aria-label={labelHidden ? "로그아웃" : undefined}
        className={cn(
          railRowClass,
          iconOnly ? "size-control-sm justify-center gap-0 px-0" : "w-full",
          !iconOnly && collapsed && railRowCollapsedClass,
          className,
        )}
        title={labelHidden ? "로그아웃" : undefined}
        type="button"
        onClick={() => void logout()}
      >
        <LogOutIcon
          aria-hidden="true"
          className="size-4 shrink-0 text-icon-default"
        />
        <span className={cn(iconOnly ? "sr-only" : collapsed && "lg:hidden")}>
          로그아웃
        </span>
      </button>
      {error ? (
        <p role="alert" className="px-2 text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
