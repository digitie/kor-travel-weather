"use client";

import {
  Activity,
  CloudSun,
  Code2,
  Database,
  Home,
  LogOut,
  MapPin,
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useState } from "react";

const groups = [
  { label: "개요", items: [{ href: "/", label: "홈", icon: Home }] },
  {
    label: "Weather source",
    items: [
      { href: "/weather", label: "날씨 지도", icon: CloudSun },
      { href: "/locations", label: "위치 카탈로그", icon: MapPin },
      { href: "/datasets", label: "데이터셋", icon: Database },
    ],
  },
  {
    label: "운영",
    items: [
      { href: "/sync-runs", label: "수집 실행", icon: Activity },
      { href: "/admin/dagster", label: "Dagster", icon: Workflow },
      { href: "/api-test", label: "API 테스트", icon: Code2 },
    ],
  },
] as const;

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [loggingOut, setLoggingOut] = useState(false);

  if (pathname === "/login") return <>{children}</>;

  async function logout() {
    setLoggingOut(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      window.location.assign("/login");
    }
  }

  return (
    <>
      <a className="skip-link" href="#main-content">
        본문으로 건너뛰기
      </a>
      <div className="app-grid" data-testid="admin-shell">
        <aside className="rail" aria-label="관리자 사이드바">
          <Link className="brand" href="/">
            <span className="brand-mark"><CloudSun size={18} strokeWidth={2.2} /></span>
            <span>kor-travel-weather</span>
            <small>operator console</small>
          </Link>
          <nav className="rail-nav" aria-label="주 메뉴">
            {groups.map((group) => (
              <div className="nav-group" key={group.label}>
                <div className="nav-section">{group.label}</div>
                {group.items.map(({ href, label, icon: Icon }) => {
                  const active = href === "/" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
                  return (
                    <Link aria-current={active ? "page" : undefined} className={`nav-link${active ? " active" : ""}`} href={href} key={href}>
                      <Icon aria-hidden="true" size={16} strokeWidth={1.8} />
                      <span>{label}</span>
                    </Link>
                  );
                })}
              </div>
            ))}
          </nav>
          <div className="rail-footer">
            <div className="operator-chip"><span className="operator-dot" /> admin</div>
            <button className="nav-link logout-button" disabled={loggingOut} onClick={() => void logout()} type="button">
              <LogOut size={16} strokeWidth={1.8} />
              <span>{loggingOut ? "로그아웃 중…" : "로그아웃"}</span>
            </button>
          </div>
        </aside>
        <main className="main" id="main-content" tabIndex={-1}>{children}</main>
      </div>
    </>
  );
}

/**
 * Page header shared by every weather console route.
 *
 * kor-travel-map keeps the route context, title, description and actions in
 * one quiet card. Keeping this as a component prevents individual pages from
 * drifting on spacing, typography, or action alignment.
 */
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
  const pathname = usePathname();

  return (
    <header className="page-header">
      <div className="page-header-copy">
        <div className="page-context">
          {section ? <span className="status context-badge">{section}</span> : null}
          <span className="page-path">{pathname}</span>
        </div>
        <h1>{title}</h1>
        {description ? <p className="description">{description}</p> : null}
      </div>
      {actions ? <div className="page-header-actions">{actions}</div> : null}
    </header>
  );
}
