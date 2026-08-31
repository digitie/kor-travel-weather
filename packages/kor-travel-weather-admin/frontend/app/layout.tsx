import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { AdminShell } from "@/components/admin-shell";
import "./globals.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata: Metadata = {
  title: "kor-travel-weather 운영",
  description: "KMA weather source administration",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html className={`${geist.variable} ${geistMono.variable}`} lang="ko">
      <body className="font-sans">
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  );
}
