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
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  AdminShell as CommonAdminShell,
  formatAdminTitle,
} from "@kor-travel/ui/admin-shell";
import { AppMenu, type AppMenuLinkProps } from "@kor-travel/ui/app-menu";

import { HelpTip } from "@/components/help-tip";
import {
  beginAdminLogout,
  cancelPendingAdminLogout,
  publishAdminLogout,
} from "@/lib/admin-auth-events";
import { cn } from "@/lib/utils";

/** 상단 바 제목의 영역 이름. 제목은 "날씨 - <페이지>" 한 줄이다. */
export const ADMIN_AREA = "날씨";

/**
 * 작업 지향 nav 그룹 — 메뉴 라벨의 단일 정본.
 * 그룹 헤더는 비링크이며 href는 존치하는 canonical 화면만 가리킨다.
 */
const NAV_GROUPS = [
  { group: null, items: [{ href: "/", label: "홈", icon: HomeIcon }] },
  {
    group: "날씨",
    items: [
      { href: "/weather", label: "날씨 지도", icon: CloudSunIcon },
      { href: "/locations", label: "위치 카탈로그", icon: MapPinIcon },
      { href: "/datasets", label: "데이터셋", icon: DatabaseIcon },
    ],
  },
  {
    group: "수집 파이프라인",
    items: [
      { href: "/sync-runs", label: "수집 실행", icon: ActivityIcon },
      { href: "/admin/dagster", label: "Dagster", icon: WorkflowIcon },
    ],
  },
  {
    group: "설정·도구",
    items: [
      { href: "/settings/providers", label: "API 키 설정", icon: KeyRoundIcon },
      { href: "/api-test", label: "API 테스트", icon: CodeIcon },
    ],
  },
] as const;

function WeatherMenuLink(props: AppMenuLinkProps) {
  return <Link {...props} />;
}

const MENU_GROUPS = NAV_GROUPS.map((group) => ({
  id: group.group ?? "root",
  label: group.group ?? undefined,
  items: group.items.map(({ href, label, icon: Icon }) => ({
    id: href,
    href,
    label,
    icon: <Icon aria-hidden="true" className="size-4" />,
  })),
}));

const NAV_HREFS: readonly string[] = NAV_GROUPS.flatMap((group) =>
  group.items.map((item) => item.href),
);

/** 가장 긴 접두로 현재 메뉴를 고른다(`/` 는 정확히 일치할 때만). */
function activeHref(pathname: string) {
  return (
    NAV_HREFS.filter((href) =>
      href === "/"
        ? pathname === "/"
        : pathname === href || pathname.startsWith(`${href}/`),
    ).sort((a, b) => b.length - a.length)[0] ?? null
  );
}

const SIDEBAR_COLLAPSED_KEY = "kor-travel-weather:sidebar-collapsed";

export type AdminShellProps = {
  /** 페이지 이름. 상단 바에 "날씨 - {title}"로 한 줄 표시한다. */
  title: string;
  /** 상단 바 제목 옆의 작은 한 줄 설명(좁으면 말줄임, 전체 문장은 title 속성). */
  description?: string;
  /** 제목 옆 도움말 팝오버 본문. 상단 바 동작 영역의 첫 아이콘 버튼이 된다. */
  help?: ReactNode;
  helpVariant?: "help" | "info";
  /** 상단 바 오른쪽 동작. 모바일 폭에서는 아이콘 버튼 1~2개로 둔다. */
  actions?: ReactNode;
  /**
   * 본문이 뷰포트의 남은 높이를 채운다(날씨 지도 작업면). 본문을 세로 flex로 만들어 자식이
   * `flex-1`로 남은 높이를 받고, 넘치면 본문 안에서만 스크롤한다.
   */
  fillViewport?: boolean;
  children: ReactNode;
};

/**
 * 날씨 관리자 화면 셸 — @kor-travel/ui `AdminShell` + `AppMenu`.
 * lg(1024px) 이상은 좌측 rail(접힘 4rem), 미만은 상단 바 메뉴 버튼으로 여는 좌측 drawer다.
 * 페이지 제목·설명·동작은 한 줄 상단 바에만 둔다(페이지 h1은 상단 바가 유일하다).
 */
export function AdminShell({
  title,
  description,
  help,
  helpVariant,
  actions,
  fillViewport = false,
  children,
}: AdminShellProps) {
  const pathname = usePathname();
  useEffect(() => {
    document.title = formatAdminTitle(ADMIN_AREA, title);
  }, [title]);
  const helpTip = help ? (
    <HelpTip label={title} variant={helpVariant}>
      {help}
    </HelpTip>
  ) : null;
  return (
    <CommonAdminShell
      actions={
        helpTip || actions ? (
          <>
            {helpTip}
            {actions}
          </>
        ) : undefined
      }
      area={ADMIN_AREA}
      brand={({ collapsed }) =>
        collapsed ? null : (
          <Link
            className="rounded-control text-text-primary no-underline"
            href="/"
          >
            {ADMIN_AREA}
          </Link>
        )
      }
      collapsedStorageKey={SIDEBAR_COLLAPSED_KEY}
      description={description}
      fillViewport={fillViewport}
      mainClassName={
        fillViewport
          ? "flex min-h-0 flex-col overflow-y-auto p-3"
          : "px-4 py-5 lg:px-6 lg:py-6"
      }
      menu={
        <AppMenu
          activeItemId={activeHref(pathname)}
          groups={MENU_GROUPS}
          label="주요 메뉴"
          linkComponent={WeatherMenuLink}
          pathname={pathname}
          testId="weather-common-menu"
        />
      }
      navigationKey={pathname}
      railFooter={({ collapsed }) => <ButtonLogout iconOnly={collapsed} />}
      testId="weather-admin-shell"
      title={title}
    >
      {children}
    </CommonAdminShell>
  );
}

const railRowClass =
  "relative flex h-control-sm w-full shrink-0 items-center gap-2.5 rounded-control px-3 text-xs font-medium whitespace-nowrap text-text-secondary transition-[color,background-color] duration-fast ease-out hover:bg-surface-subtle hover:text-text-primary active:bg-surface-muted";

function ButtonLogout({ iconOnly = false }: { iconOnly?: boolean }) {
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
        aria-label={iconOnly ? "로그아웃" : undefined}
        className={cn(railRowClass, iconOnly && "justify-center gap-0 px-0")}
        title={iconOnly ? "로그아웃" : undefined}
        type="button"
        onClick={() => void logout()}
      >
        <LogOutIcon
          aria-hidden="true"
          className="size-4 shrink-0 text-icon-default"
        />
        <span className={cn(iconOnly && "sr-only")}>로그아웃</span>
      </button>
      {error ? (
        <p role="alert" className="px-2 text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
