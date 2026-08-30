"use client";

import { Activity, CloudSun, Database, Home, MapPin } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const groups = [
  { label: "개요", items: [{ href: "/", label: "홈", icon: Home }] },
  { label: "Weather source", items: [{ href: "/locations", label: "위치 카탈로그", icon: MapPin }, { href: "/weather", label: "최신 날씨", icon: CloudSun }] },
  { label: "운영", items: [{ href: "/sync-runs", label: "수집 실행", icon: Activity }, { href: "/datasets", label: "데이터셋", icon: Database }] },
] as const;

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="app-grid">
      <aside className="rail">
        <div className="brand">kor-travel-weather<small>operator console</small></div>
        {groups.map((group) => (
          <div key={group.label}>
            <div className="nav-section">{group.label}</div>
            {group.items.map(({ href, label, icon: Icon }) => {
              const active = href === "/" ? pathname === href : pathname.startsWith(href);
              return <Link className={`nav-link${active ? " active" : ""}`} href={href} key={href}><Icon size={15} strokeWidth={1.8} />{label}</Link>;
            })}
          </div>
        ))}
      </aside>
      <main className="main" id="main-content">{children}</main>
    </div>
  );
}
