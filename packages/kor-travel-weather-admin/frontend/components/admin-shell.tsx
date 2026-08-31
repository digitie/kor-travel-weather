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
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

type NavigationItem = {
  href: string;
  label: string;
  icon: typeof Home;
};

// Keep the same flat rail recipe as kor-travel-map. Weather destinations use
// the same icon/label/link treatment while retaining weather-specific routes.
const NAV_ITEMS: NavigationItem[] = [
  { href: "/", label: "홈", icon: Home },
  { href: "/weather", label: "날씨 지도", icon: CloudSun },
  { href: "/locations", label: "위치 카탈로그", icon: MapPin },
  { href: "/datasets", label: "데이터셋", icon: Database },
  { href: "/sync-runs", label: "수집 실행", icon: Activity },
  { href: "/settings/providers", label: "API 키 설정", icon: KeyRound },
  { href: "/admin/dagster", label: "Dagster", icon: Workflow },
  { href: "/api-test", label: "API 테스트", icon: Code2 },
];

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
  const [loggingOut, setLoggingOut] = useState(false);
  const activeItem = [...NAV_ITEMS]
    .filter((item) => isActive(pathname, item.href))
    .sort((left, right) => right.href.length - left.href.length)[0];

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
      <div className="app-grid" data-testid="admin-shell">
        <aside className="rail" aria-label="관리자 사이드바" data-slot="admin-shell-rail">
          <div className="rail-shell">
            <div className="rail-header">
              <Link className="brand" href="/" aria-label="kor-travel-weather admin">
                <span className="brand-mark" aria-hidden="true"><CloudSun size={17} /></span>
                <span className="brand-wordmark">kor-travel-weather</span>
                <span className="brand-subtitle">admin</span>
              </Link>
            </div>
            <nav className="rail-nav" aria-label="주요 메뉴">
              {NAV_ITEMS.map((item) => {
                const active = item.href === activeItem?.href;
                const Icon = item.icon;
                return (
                  <Link
                    aria-current={active ? "page" : undefined}
                    className={`nav-link${active ? " active" : ""}`}
                    href={item.href}
                    key={item.href}
                  >
                    <Icon aria-hidden="true" size={16} strokeWidth={1.8} />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
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
            <span className="page-path">{usePathname()}</span>
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
