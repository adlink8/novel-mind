"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  BookOpenText,
  Feather,
  LibraryBig,
  Search,
  Settings2,
  Sparkles,
} from "lucide-react";

import { cn } from "@/lib/utils";

const navigation = [
  { href: "/", label: "工作台", icon: Sparkles },
  { href: "/novels", label: "书架", icon: LibraryBig },
  { href: "/search", label: "检索", icon: Search },
  { href: "/eval", label: "评测", icon: BarChart3 },
  { href: "/writing", label: "创作", icon: Feather },
  { href: "/settings", label: "设置", icon: Settings2 },
];

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen bg-background text-foreground">
      <a
        href="#main-content"
        className="fixed left-4 top-4 z-[100] -translate-y-20 rounded-full bg-foreground px-4 py-2 text-sm font-medium text-background transition-transform focus:translate-y-0"
      >
        跳到主内容
      </a>

      <aside className="fixed inset-y-4 left-4 z-40 hidden w-60 flex-col rounded-[28px] border border-white/60 bg-sidebar/90 p-3 shadow-[0_24px_70px_-30px_rgba(38,31,24,0.38)] backdrop-blur-xl lg:flex">
        <Link href="/" className="flex items-center gap-3 rounded-2xl px-3 py-3">
          <span className="grid size-11 place-items-center rounded-2xl bg-foreground text-background shadow-sm">
            <BookOpenText className="size-5" />
          </span>
          <span>
            <span className="block font-serif text-xl font-semibold tracking-tight">NovelMind</span>
            <span className="block text-[11px] uppercase tracking-[0.18em] text-muted-foreground">Story intelligence</span>
          </span>
        </Link>

        <nav aria-label="主导航" className="mt-7 space-y-1.5">
          {navigation.map((item) => {
            const active = isActive(pathname, item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "group flex items-center gap-3 rounded-2xl px-3 py-2.5 text-sm font-medium transition-all duration-200",
                  active
                    ? "bg-foreground text-background shadow-sm"
                    : "text-muted-foreground hover:bg-white/75 hover:text-foreground",
                )}
              >
                <Icon className="size-[18px]" strokeWidth={active ? 2.2 : 1.8} />
                <span>{item.label}</span>
                {active && <span className="ml-auto size-1.5 rounded-full bg-primary" />}
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto rounded-2xl border border-border/70 bg-white/65 p-4">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            <Sparkles className="size-3.5 text-primary" />
            AI workspace
          </div>
          <p className="text-sm font-medium leading-5">从原文证据出发，理解、检索与创作。</p>
        </div>
      </aside>

      <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-border/70 bg-background/85 px-4 backdrop-blur-xl lg:hidden">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="grid size-9 place-items-center rounded-xl bg-foreground text-background">
            <BookOpenText className="size-4" />
          </span>
          <span className="font-serif text-lg font-semibold">NovelMind</span>
        </Link>
        <Link href="/search" className="grid size-10 place-items-center rounded-full border bg-card" aria-label="搜索">
          <Search className="size-4" />
        </Link>
      </header>

      <main id="main-content" className="min-h-screen pb-24 lg:ml-[276px] lg:pb-0">
        {children}
      </main>

      <nav aria-label="移动导航" className="fixed inset-x-3 bottom-3 z-50 grid grid-cols-6 rounded-[22px] border border-white/60 bg-sidebar/95 p-1.5 shadow-[0_18px_50px_-22px_rgba(38,31,24,0.65)] backdrop-blur-xl lg:hidden">
        {navigation.map((item) => {
          const active = isActive(pathname, item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-label={item.label}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex min-w-0 flex-col items-center gap-1 rounded-2xl px-1 py-2 text-[10px] font-medium transition-colors",
                active ? "bg-foreground text-background" : "text-muted-foreground",
              )}
            >
              <Icon className="size-4" />
              <span className="truncate">{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
