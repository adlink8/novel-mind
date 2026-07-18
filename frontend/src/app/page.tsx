"use client";

import Link from "next/link";
import { ArrowRight, BarChart3, BookOpenText, Feather, FileSearch, LibraryBig, Plus, Sparkles } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { FlipBook, type FlipBookPage } from "@/components/flip-book";
import { cn } from "@/lib/utils";
import { PageContainer } from "@/components/page-header";
import { useNovels } from "@/hooks/use-novels";

const quickActions = [
  { title: "导入原文", description: "建立章节结构与语义索引", icon: Plus, href: "/novels?action=import" },
  { title: "证据检索", description: "在全部作品中查找原文线索", icon: FileSearch, href: "/search" },
  { title: "检索评测", description: "比较 BM25 与混合检索质量", icon: BarChart3, href: "/eval" },
  { title: "创作草稿", description: "从原作分支点开始新叙事", icon: Feather, href: "/writing" },
];

export default function HomePage() {
  const { novels, loading } = useNovels();
  const chapterTotal = novels.reduce((sum, novel) => sum + novel.chapter_count, 0);
  const wordTotal = novels.reduce((sum, novel) => sum + novel.word_count, 0);
  const recentNovels = [...novels].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()).slice(0, 3);

  const stats = [
    { label: "已入库作品", value: loading ? "—" : `${novels.length}`, suffix: "本", icon: LibraryBig },
    { label: "可阅读章节", value: loading ? "—" : chapterTotal.toLocaleString("zh-CN"), suffix: "章", icon: BookOpenText },
    { label: "原文字数", value: loading ? "—" : wordTotal >= 10000 ? `${(wordTotal / 10000).toFixed(1)}万` : wordTotal.toLocaleString("zh-CN"), suffix: "字", icon: FileSearch },
  ];

  // ── 书页：目录 → 最近作品 → 藏书一览（两种布局共用） ──
  const bookPages: FlipBookPage[] = [
    {
      id: "contents",
      front: (
        <div className="flex h-full flex-col p-6 pr-12 xl:p-8 xl:pr-14">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary">目录 · Contents</p>
          <div className="mt-4 flex-1 space-y-1.5">
            {quickActions.map((action) => (
              <Link key={action.title} href={action.href} className="group flex items-center gap-3 rounded-xl px-3 py-2.5 text-foreground transition-colors hover:bg-secondary">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-secondary text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground"><action.icon className="size-4" /></span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold">{action.title}</span>
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">{action.description}</span>
                </span>
                <ArrowRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
              </Link>
            ))}
          </div>
          <p className="text-right text-xs text-muted-foreground/60">壹</p>
        </div>
      ),
    },
    {
      id: "recent",
      front: (
        <div className="flex h-full flex-col p-6 pr-12 xl:p-8 xl:pr-14">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary">最近的作品 · Continue reading</p>
          <div className="mt-4 flex-1 space-y-1.5">
            {recentNovels.length > 0 ? recentNovels.map((novel, index) => (
              <Link key={novel.id} href={`/novels/${novel.id}`} className="group flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors hover:bg-secondary">
                <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-secondary font-serif text-sm font-semibold text-primary">{String(index + 1).padStart(2, "0")}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-foreground">{novel.title}</span>
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">{novel.author || "未知作者"} · {novel.chapter_count} 章</span>
                </span>
                <ArrowRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
              </Link>
            )) : (
              <div className="grid h-full place-items-center text-center">
                <div>
                  <BookOpenText className="mx-auto size-7 text-muted-foreground" />
                  <p className="mt-2 text-sm text-muted-foreground">书架还空着，等第一本书。</p>
                </div>
              </div>
            )}
          </div>
          <div className="flex items-center justify-between">
            <Link href="/novels" className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">全部书架 <ArrowRight className="size-3" /></Link>
            <p className="text-xs text-muted-foreground/60">贰</p>
          </div>
        </div>
      ),
    },
    {
      id: "stats",
      front: (
        <div className="flex h-full flex-col p-6 xl:p-8">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary">藏书一览 · Library</p>
          <div className="mt-5 flex-1 space-y-4 px-1">
            {stats.map((stat) => (
              <div key={stat.label} className="flex items-center justify-between border-b border-border/50 pb-3">
                <span className="flex items-center gap-2 text-sm text-muted-foreground"><stat.icon className="size-4 text-primary" />{stat.label}</span>
                <span className="font-serif text-2xl font-semibold text-foreground">{stat.value}<span className="ml-1 text-xs font-normal text-muted-foreground">{stat.suffix}</span></span>
              </div>
            ))}
          </div>
          <p className="text-right text-xs text-muted-foreground/60">叁</p>
        </div>
      ),
    },
  ];

  const backCover = (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
      <span className="font-serif text-2xl text-primary">❦</span>
      <p className="font-serif text-xl font-semibold text-foreground">故事，值得被记住</p>
      <p className="max-w-xs text-sm leading-6 text-muted-foreground">从原文证据出发，理解、检索与创作。</p>
      <Link href="/novels" className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-primary px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/90">进入书架 <ArrowRight className="size-4" /></Link>
    </div>
  );

  return (
    <PageContainer className="py-6 sm:py-8">
      {/* ── 桌面端：整页对开 3D 动态翻页书（倾斜开卷 + 悬浮 + 金光） ── */}
      <div className="hidden lg:flex lg:min-h-[86vh] lg:items-center lg:justify-center">
        <FlipBook
          pages={bookPages}
          tone="on-light"
          ambient
          ariaLabel="NovelMind 互动导览书"
          className="w-full max-w-[1220px] px-6 xl:px-10"
          insideCover={
            <div className="flex h-full flex-col justify-center p-10 xl:p-12">
              <div className="mb-6 inline-flex w-fit items-center gap-2 rounded-full border border-primary/25 bg-primary/10 px-3 py-1.5 text-xs tracking-wide text-primary">
                <Sparkles className="size-3.5" /> AI 原文研究与创作空间
              </div>
              <h1 className="font-serif text-4xl font-semibold leading-[1.15] tracking-[-0.025em] text-foreground xl:text-[2.9rem]">让每一段故事，<br />都有迹可循。</h1>
              <p className="mt-4 max-w-md text-sm leading-7 text-muted-foreground">导入长篇文本，建立可检索的故事记忆。沿着人物、事件与原文证据，完成理解、评测与再创作。</p>
              <div className="mt-7 flex flex-wrap gap-3">
                <Link href="/novels?action=import" className={cn(buttonVariants({ size: "lg" }), "h-11 rounded-full bg-primary px-6 text-white hover:bg-primary/90")}>导入第一本小说 <ArrowRight className="ml-2 size-4" /></Link>
                <Link href="/search" className={cn(buttonVariants({ size: "lg", variant: "outline" }), "h-11 rounded-full px-6")}>开始检索</Link>
              </div>
              <p className="mt-8 text-xs text-muted-foreground/70">点右页边缘翻开目录</p>
            </div>
          }
          insideBackCover={backCover}
        />
      </div>

      {/* ── 窄屏：同一本书的单页竖版形态（封面即首页） ── */}
      <div className="flex justify-center py-4 lg:hidden">
        <FlipBook
          layout="single"
          pages={bookPages}
          tone="on-light"
          ambient
          ariaLabel="NovelMind 互动导览书"
          className="w-full max-w-[430px]"
          insideCover={
            <div className="flex h-full flex-col justify-center p-7">
              <div className="mb-5 inline-flex w-fit items-center gap-2 rounded-full border border-primary/25 bg-primary/10 px-3 py-1 text-[11px] tracking-wide text-primary">
                <Sparkles className="size-3" /> AI 原文研究与创作空间
              </div>
              <h1 className="font-serif text-[27px] font-semibold leading-[1.2] tracking-[-0.02em] text-foreground">让每一段故事，<br />都有迹可循。</h1>
              <p className="mt-3 text-[13px] leading-6 text-muted-foreground">导入长篇文本，建立可检索的故事记忆。沿着人物、事件与原文证据，完成理解、评测与再创作。</p>
              <div className="mt-6 flex flex-col items-center gap-2.5">
                <Link href="/novels?action=import" className={cn(buttonVariants({ size: "lg" }), "h-10 w-fit rounded-full bg-primary px-5 text-white hover:bg-primary/90")}>导入第一本小说 <ArrowRight className="ml-2 size-4" /></Link>
                <Link href="/search" className={cn(buttonVariants({ size: "lg", variant: "outline" }), "h-10 w-fit rounded-full px-5")}>开始检索</Link>
              </div>
              <p className="mt-6 text-center text-[11px] text-muted-foreground/70">点右缘翻页</p>
            </div>
          }
          insideBackCover={backCover}
        />
      </div>
    </PageContainer>
  );
}
