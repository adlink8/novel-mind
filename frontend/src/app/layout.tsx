/**
 * 根布局组件 (Next.js App Router)
 *
 * 定义全局 HTML 结构:
 * - <html lang="zh-CN">: 中文页面
 * - Inter 字体: Google Fonts 加载的无衬线字体
 * - 侧边栏导航: 工作台/书架/分析/评测/创作/设置中心
 * - 主内容区: 右侧可滚动区域，渲染子页面
 *
 * 响应式:
 * - lg 以上: 显示侧边导航栏
 * - lg 以下: 顶部栏 + 底部移动导航
 *
 * Phase 18: pre-paint theme bootstrap (no FOUC) + AppThemeSync reconciler.
 */

import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Inter, Noto_Serif_SC } from "next/font/google";
import { cn } from "@/lib/utils";
import { AuthGate } from "@/components/auth-gate";
import { AppShell } from "@/components/app-shell";
import { AppThemeSync } from "@/components/app-theme-sync";
import { THEME_BOOT_SCRIPT } from "@/components/reader/reader-preferences";

const inter = Inter({subsets:["latin"], variable:"--font-sans"});
const notoSerifSC = Noto_Serif_SC({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-serif",
});

export const metadata: Metadata = {
  title: "NovelMind - AI 辅助小说创作与理解",
  description: "让 AI 成为你的小说伙伴 —— 读懂故事、理清脉络、续写篇章",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // 刘海屏全屏绘制；safe-area 留白由各悬浮层自行处理
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className={cn("font-sans", inter.variable, notoSerifSC.variable)} suppressHydrationWarning>
      <head>
        <script
          // Defensive pre-paint theme restore — same key/validation as AppThemeSync.
          dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }}
        />
      </head>
      <body className="min-h-screen bg-background font-sans antialiased">
        <AppThemeSync />
        <AuthGate><AppShell>{children}</AppShell></AuthGate>
      </body>
    </html>
  );
}
