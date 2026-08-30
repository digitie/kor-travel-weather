import type { Metadata } from "next";

import { AdminShell } from "@/components/admin-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "kor-travel-weather 운영",
  description: "KMA weather source administration",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  );
}
