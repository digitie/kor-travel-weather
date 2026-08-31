"use client";

/* Hallmark · genre: editorial-utilitarian · macrostructure: Rail-Workbench · design-system: kor-travel-map */

import {
  Activity,
  CloudSun,
  Code2,
  Database,
  Home,
  KeyRound,
  LogOut,
  MapPin,
  PanelLeftClose,
  PanelLeftOpen,
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

type NavigationItem = {
  href: string;
  label: string;
  icon: typeof Home;
};

type NavigationGroup = {
  group: string | null;
  items: NavigationItem[];
};

// Keep the same grouped rail recipe as kor-travel-map. Weather destinations
// are slotted into the same operator phases so both consoles feel identical.
const NAV_GROUPS: NavigationGroup[] = [
  { group: null, items: [{ href: "/", label: "홈", icon: Home }] },
  {
    group: "날씨 탐색",
    items: [
      { href: "/weather", label: "날씨 지도", icon: CloudSun },
      { href: "/locations", label: "위치 카탈로그", icon: MapPin },
    ],
  },
  {
    group: "수집 파이프라인",
    items: [
      { href: "/datasets", label: "데이터셋", icon: Database },
      { href: "/sync-runs", label: "수집 실행", icon: Activity },
    ],
  },
  {
    group: "시스템",
    items: [
      { href: "/settings/providers", label: "API 키 설정", icon: KeyRound },
      { href: "/admin/dagster", label: "Dagster", icon: Workflow },
      { href: "/api-test", label: "API 테스트", icon: Code2 },
    ],
  },
];

const NAV_ITEMS = NAV_GROUPS.flatMap((group) => group.items);
const SIDEBAR_COLLAPSED_KEY = "kor-travel-weather:sidebar-collapsed";

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

function logout() {
  return fetch("/api/auth/logout", { method: "POST" }).finally(() => {
    window.location.assign("/login");
  });
}

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const activeItemRef = useRef<HTMLAnchorElement | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
  });
  const [loggingOut, setLoggingOut] = useState(false);
  const activeItem = [...NAV_ITEMS]
    .filter((item) => isActive(pathname, item.href))
    .sort((left, right) => right.href.length - left.href.length)[0];

  useEffect(() => {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed]);

  useEffect(() => {
    if (window.innerWidth >= 1024) return;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    activeItemRef.current?.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "nearest",
      inline: "center",
    });
  }, [activeItem?.href]);

  async function handleLogout() {
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      setLoggingOut(false);
    }
  }

  if (pathname === "/login") return <>{children}</>;

  return (
    <div className="weather-shell">
      <a className="skip-link" href="#main-content">본문으로 건너뛰기</a>
      <div className={`app-grid${sidebarCollapsed ? " sidebar-collapsed" : ""}`} data-testid="admin-shell">
        <aside className="rail" aria-label="관리자 사이드바" data-slot="admin-shell-rail">
          <div className="rail-shell">
            <div className="rail-header">
              <Link className="brand" href="/" aria-label="kor-travel-weather admin">
                <span className="brand-wordmark">kor-travel-weather</span>
                <span className="brand-subtitle">admin</span>
                <span className="brand-short" aria-hidden="true">ktw</span>
              </Link>
              <div className="rail-header-actions">
                <button
                  aria-label="로그아웃"
                  className="rail-icon-button rail-mobile-logout"
                  disabled={loggingOut}
                  onClick={() => void handleLogout()}
                  title="로그아웃"
                  type="button"
                >
                  <LogOut aria-hidden="true" size={16} />
                </button>
                <button
                  aria-label={sidebarCollapsed ? "좌측 메뉴 펼치기" : "좌측 메뉴 접기"}
                  className="rail-icon-button rail-collapse"
                  onClick={() => setSidebarCollapsed((value) => !value)}
                  title={sidebarCollapsed ? "좌측 메뉴 펼치기" : "좌측 메뉴 접기"}
                  type="button"
                >
                  {sidebarCollapsed ? <PanelLeftOpen aria-hidden="true" size={16} /> : <PanelLeftClose aria-hidden="true" size={16} />}
                </button>
              </div>
            </div>
            <nav className="rail-nav" aria-label="주요 메뉴">
              {NAV_GROUPS.map((group) => (
                <div className="nav-group" key={group.group ?? "overview"}>
                  {group.group ? <div className="nav-section"><span>{group.group}</span><i aria-hidden="true" /></div> : null}
                  {group.items.map((item) => {
                    const active = item.href === activeItem?.href;
                    const Icon = item.icon;
                    return (
                      <Link
                        aria-current={active ? "page" : undefined}
                        aria-label={sidebarCollapsed ? item.label : undefined}
                        className={`nav-link${active ? " active" : ""}`}
                        href={item.href}
                        key={item.href}
                        ref={active ? activeItemRef : undefined}
                        title={sidebarCollapsed ? item.label : undefined}
                      >
                        <Icon aria-hidden="true" size={16} strokeWidth={1.8} />
                        <span>{item.label}</span>
                      </Link>
                    );
                  })}
                </div>
              ))}
            </nav>
            <div className="rail-footer">
              <button
                className="nav-link logout-button"
                disabled={loggingOut}
                onClick={() => void handleLogout()}
                type="button"
              >
                <LogOut aria-hidden="true" size={16} strokeWidth={1.8} />
                <span>로그아웃</span>
              </button>
            </div>
          </div>
        </aside>
        <main className="main" id="main-content" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  section,
  actions,
}: {
  title: string;
  description?: string;
  section?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="page-header-wrap" data-slot="admin-shell-header">
      <div className="page-header">
        <div className="page-header-copy">
          <div className="page-context">
            {section ? <span className="page-section">{section}</span> : null}
          </div>
          <div className="page-header-row">
            <h1>{title}</h1>
            {actions ? <div className="page-header-actions">{actions}</div> : null}
          </div>
          {description ? <p className="description">{description}</p> : null}
        </div>
      </div>
    </header>
  );
}
