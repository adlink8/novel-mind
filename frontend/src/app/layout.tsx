import type { Metadata } from "next";
import "./globals.css";

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
    <html lang="zh-CN">
      <body className="min-h-screen bg-background font-sans antialiased">
        <div className="flex h-screen">
          {/* 侧边栏 */}
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

          {/* 主内容区 */}
          <main className="flex-1 overflow-auto">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}

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
