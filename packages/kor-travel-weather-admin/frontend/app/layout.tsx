import type { Metadata } from "next";
import { Geist } from "next/font/google";

import { AdminShell } from "@/components/admin-shell";
import "./globals.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "kor-travel-weather 운영",
  description: "KMA weather source administration",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" className={geist.variable}>
      <body className="font-sans">
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  );
}
