"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  BarChart3,
  BookOpenText,
  Feather,
  LibraryBig,
  ListTree,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings2,
  Sparkles,
} from "lucide-react";

import { cn } from "@/lib/utils";

const navigation = [
  { href: "/", label: "工作台", icon: Sparkles },
  { href: "/novels", label: "书架", icon: LibraryBig },
  { href: "/analysis", label: "分析", icon: ListTree },
  { href: "/eval", label: "评测", icon: BarChart3 },
  { href: "/writing", label: "创作", icon: Feather },
  { href: "/settings", label: "设置中心", icon: Settings2 },
];

const SHELL_NAV_KEY = "novelmind:app-shell:nav-collapsed";

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function loadNavCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(SHELL_NAV_KEY) === "1";
  } catch {
    return false;
  }
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setNavCollapsed(loadNavCollapsed());
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    try {
      window.localStorage.setItem(SHELL_NAV_KEY, navCollapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [navCollapsed, hydrated]);

  const toggleNav = () => setNavCollapsed((c) => !c);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <a
        href="#main-content"
        className="fixed left-4 top-4 z-[100] -translate-y-20 rounded-full bg-foreground px-4 py-2 text-sm font-medium text-background transition-transform focus:translate-y-0"
      >
        跳到主内容
      </a>

      {/* Desktop: collapsible workspace nav rail */}
      <aside
        data-testid="app-shell-nav"
        data-collapsed={navCollapsed ? "true" : "false"}
        className={cn(
          "fixed inset-y-4 left-4 z-40 hidden flex-col overflow-hidden rounded-[28px] border border-border/70 bg-sidebar/90 p-3 shadow-[0_24px_70px_-30px_rgba(38,31,24,0.38)] backdrop-blur-xl transition-[width,box-shadow] motion-duration-spatial motion-ease-enter lg:flex",
          navCollapsed ? "w-[4.5rem]" : "w-60",
        )}
      >
        <div
          className={cn(
            "flex items-center gap-2",
            navCollapsed ? "flex-col" : "justify-between",
          )}
        >
          <Link
            href="/"
            className={cn(
              "flex min-w-0 items-center gap-3 rounded-2xl py-2",
              navCollapsed ? "px-0" : "px-3",
            )}
            title="NovelMind"
          >
            <span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-foreground text-background shadow-sm">
              <BookOpenText className="size-5" />
            </span>
            {!navCollapsed ? (
              <span className="min-w-0">
                <span className="block font-serif text-xl font-semibold tracking-tight">
                  NovelMind
                </span>
                <span className="block text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                  Story intelligence
                </span>
              </span>
            ) : null}
          </Link>
          <button
            type="button"
            data-testid="app-shell-nav-toggle"
            aria-label={navCollapsed ? "展开工作台导航" : "收起工作台导航"}
            aria-expanded={!navCollapsed}
            className="grid size-9 shrink-0 place-items-center rounded-xl text-muted-foreground transition-colors hover:bg-card/80 hover:text-foreground"
            onClick={toggleNav}
          >
            {navCollapsed ? (
              <PanelLeftOpen className="size-4" />
            ) : (
              <PanelLeftClose className="size-4" />
            )}
          </button>
        </div>

        <nav aria-label="主导航" className="mt-7 space-y-1.5">
          {navigation.map((item) => {
            const active = isActive(pathname, item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                title={item.label}
                className={cn(
                  "group flex items-center gap-3 rounded-2xl py-2.5 text-sm font-medium transition-[color,background-color,box-shadow] motion-duration-standard motion-ease-enter",
                  navCollapsed ? "justify-center px-2" : "px-3",
                  active
                    ? "bg-foreground text-background shadow-sm"
                    : "text-muted-foreground hover:bg-card/75 hover:text-foreground",
                )}
              >
                <Icon className="size-[18px] shrink-0" strokeWidth={active ? 2.2 : 1.8} />
                {!navCollapsed ? (
                  <>
                    <span>{item.label}</span>
                    {active ? (
                      <span className="ml-auto size-1.5 rounded-full bg-primary" />
                    ) : null}
                  </>
                ) : (
                  <span className="sr-only">{item.label}</span>
                )}
              </Link>
            );
          })}
        </nav>

        {!navCollapsed ? (
          <div className="mt-auto rounded-2xl border border-border/70 bg-card/65 p-4">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              <Sparkles className="size-3.5 text-primary" />
              AI workspace
            </div>
            <p className="text-sm font-medium leading-5">
              从原文证据出发，理解、检索与创作。
            </p>
          </div>
        ) : (
          <div className="mt-auto flex justify-center pb-1" title="AI workspace">
            <Sparkles className="size-4 text-primary" aria-hidden />
          </div>
        )}
      </aside>

      <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-border/70 bg-background/85 px-4 backdrop-blur-xl lg:hidden">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="grid size-9 place-items-center rounded-xl bg-foreground text-background">
            <BookOpenText className="size-4" />
          </span>
          <span className="font-serif text-lg font-semibold">NovelMind</span>
        </Link>
        <Link
          href="/search"
          className="grid size-10 place-items-center rounded-full border bg-card"
          aria-label="搜索"
        >
          <Search className="size-4" />
        </Link>
      </header>

      <main
        id="main-content"
        className={cn(
          "transition-[margin] motion-duration-spatial motion-ease-enter",
          // Analysis workbench fills the viewport; other pages keep classic scroll + mobile bottom-nav pad
          pathname.startsWith("/analysis")
            ? "h-[calc(100dvh-4rem)] overflow-hidden pb-0 lg:h-dvh"
            : "min-h-screen pb-24 lg:pb-0",
          navCollapsed ? "lg:ml-[5.75rem]" : "lg:ml-[276px]",
        )}
      >
        {children}
      </main>

      <nav
        aria-label="移动导航"
        className="fixed inset-x-3 bottom-3 z-50 grid grid-cols-6 rounded-[22px] border border-border/70 bg-sidebar/95 p-1.5 shadow-[0_18px_50px_-22px_rgba(38,31,24,0.65)] backdrop-blur-xl lg:hidden"
      >
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
                "flex min-w-0 flex-col items-center gap-1 rounded-2xl px-1 py-2 text-[10px] font-medium transition-[color,background-color] motion-duration-fast motion-ease-enter",
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
