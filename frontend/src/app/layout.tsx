/**
 * 根布局组件 (Next.js App Router)
 *
 * 定义全局 HTML 结构:
 * - <html lang="zh-CN">: 中文页面
 * - Inter 字体: Google Fonts 加载的无衬线字体
 * - 侧边栏导航: 左侧固定 64px 宽的导航栏（仪表盘/书架/创作中心/AI设置）
 * - 主内容区: 右侧可滚动区域，渲染子页面
 *
 * 响应式:
 * - md 以上: 显示侧边栏
 * - md 以下: 隐藏侧边栏（未来可加移动端汉堡菜单）
 */

import type { Metadata } from "next";
import "./globals.css";
import { Inter } from "next/font/google";
import { cn } from "@/lib/utils";
import { AuthGate } from "@/components/auth-gate";
import { AppShell } from "@/components/app-shell";

const inter = Inter({subsets:["latin"], variable:"--font-sans"});

export const metadata: Metadata = {
  title: "NovelMind - AI 辅助小说创作与理解",
  description: "让 AI 成为你的小说伙伴 —— 读懂故事、理清脉络、续写篇章",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className={cn("font-sans", inter.variable)}>
      <body className="min-h-screen bg-background font-sans antialiased">
        <AuthGate><AppShell>{children}</AppShell></AuthGate>
      </body>
    </html>
  );
}
