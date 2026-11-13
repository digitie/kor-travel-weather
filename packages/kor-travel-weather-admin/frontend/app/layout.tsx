/**
 * Next.js 16 App Router 루트 레이아웃 — kor-travel-map admin `src/app/layout.tsx`와 같은 구조.
 *
 * - 셸(AdminShell)은 페이지마다 감싼다(map과 같은 방식). 로그인 화면은 셸 없이 렌더한다.
 * - 서체: 본문/UI = Pretendard Variable(@kor-travel/tokens/font.css), mono = Geist Mono(next/font).
 */

import type { Metadata } from "next";
import { Geist_Mono } from "next/font/google";
import type { ReactNode } from "react";

import { ConfirmDialogProvider } from "@/components/confirm-dialog";
import { Toaster } from "@/components/ui/sonner";
import { cn } from "@/lib/utils";
import { AppQueryClientProvider } from "@/providers/query-client-provider";

import "@kor-travel/tokens/font.css";
import "./globals.css";

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
});

export const metadata: Metadata = {
  title: {
    default: "Weather Scraper Admin UI",
    template: "%s · Weather Scraper Admin UI",
  },
  description: "Weather Scraper 운영·수집 관리 화면",
};

interface RootLayoutProps {
  children: ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="ko" className={cn("font-sans", geistMono.variable)}>
      <body>
        <AppQueryClientProvider>
          <ConfirmDialogProvider>{children}</ConfirmDialogProvider>
          <Toaster position="top-right" />
        </AppQueryClientProvider>
      </body>
    </html>
  );
}
