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
        <div className="flex h-screen">
          {/* 侧边栏导航（桌面端可见） */}
          <aside className="hidden md:flex w-64 flex-col border-r bg-card">
            <div className="p-6">
              <h1 className="text-xl font-bold text-primary">NovelMind</h1>
              <p className="text-xs text-muted-foreground mt-1">AI 小说助手</p>
            </div>
            <nav className="flex-1 px-3 space-y-1">
              <SidebarLink href="/" icon="📊" label="仪表盘" />
              <SidebarLink href="/novels" icon="📚" label="我的书架" />
              <SidebarLink href="/writing" icon="✍️" label="创作中心" />
              <SidebarLink href="/settings" icon="⚙️" label="AI 设置" />
            </nav>
            <div className="p-4 border-t text-xs text-muted-foreground">
              v0.1.0 · MVP
            </div>
          </aside>

          {/* 主内容区（占满剩余宽度） */}
          <main className="flex-1 overflow-auto">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}

/**
 * 侧边栏导航链接组件
 * 渲染图标 + 文字的水平布局，hover 时高亮背景
 */
function SidebarLink({
  href,
  icon,
  label,
}: {
  href: string;
  icon: string;
  label: string;
}) {
  return (
    <a
      href={href}
      className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm hover:bg-accent transition-colors"
    >
      <span>{icon}</span>
      <span>{label}</span>
    </a>
  );
}
