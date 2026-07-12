"use client";

import Link from "next/link";
import { ArrowRight, BarChart3, BookOpenText, Feather, FileSearch, LibraryBig, Plus, Sparkles } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
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

  return (
    <PageContainer className="space-y-8">
      <section className="relative overflow-hidden rounded-[32px] bg-foreground px-6 py-9 text-background shadow-[0_30px_80px_-38px_rgba(44,34,25,0.7)] sm:px-10 sm:py-12 xl:px-14 xl:py-14">
        <div className="absolute inset-y-0 right-0 hidden w-[42%] border-l border-white/10 lg:block">
          <div className="absolute inset-0 opacity-30 [background-image:linear-gradient(hsl(42_35%_96%/0.13)_1px,transparent_1px),linear-gradient(90deg,hsl(42_35%_96%/0.13)_1px,transparent_1px)] [background-size:38px_38px]" />
          <div className="absolute left-14 top-16 font-serif text-[11rem] leading-none text-white/[0.06]">N</div>
        </div>
        <div className="relative max-w-3xl">
          <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1.5 text-xs tracking-wide text-white/75">
            <Sparkles className="size-3.5 text-[#f1a27b]" /> AI 原文研究与创作空间
          </div>
          <h1 className="font-serif text-4xl font-semibold leading-[1.08] tracking-[-0.035em] sm:text-5xl xl:text-6xl">让每一段故事，<br className="hidden sm:block" />都有迹可循。</h1>
          <p className="mt-5 max-w-xl text-sm leading-7 text-white/65 sm:text-base">导入长篇文本，建立可检索的故事记忆。沿着人物、事件与原文证据，完成理解、评测与再创作。</p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/novels?action=import" className={cn(buttonVariants({ size: "lg" }), "h-11 rounded-full bg-[#e7794d] px-6 text-white hover:bg-[#d8693f]")}>导入第一本小说 <ArrowRight className="ml-2 size-4" /></Link>
            <Link href="/search" className={cn(buttonVariants({ size: "lg", variant: "outline" }), "h-11 rounded-full border-white/20 bg-white/5 px-6 text-white hover:bg-white/10 hover:text-white")}>开始检索</Link>
          </div>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        {[
          { label: "已入库作品", value: loading ? "—" : `${novels.length}`, suffix: "本", icon: LibraryBig },
          { label: "可阅读章节", value: loading ? "—" : chapterTotal.toLocaleString("zh-CN"), suffix: "章", icon: BookOpenText },
          { label: "原文字数", value: loading ? "—" : wordTotal >= 10000 ? `${(wordTotal / 10000).toFixed(1)}万` : wordTotal.toLocaleString("zh-CN"), suffix: "字", icon: FileSearch },
        ].map((stat) => (
          <div key={stat.label} className="paper-surface rounded-3xl p-5 sm:p-6">
            <div className="flex items-center justify-between"><span className="text-sm text-muted-foreground">{stat.label}</span><stat.icon className="size-4 text-primary" /></div>
            <p className="mt-5 font-serif text-3xl font-semibold tracking-tight">{stat.value}<span className="ml-1 text-sm font-sans font-normal text-muted-foreground">{stat.suffix}</span></p>
          </div>
        ))}
      </section>

      <section className="grid gap-8 xl:grid-cols-[1.15fr_0.85fr]">
        <div>
          <div className="mb-4 flex items-end justify-between"><div><p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary">Continue reading</p><h2 className="mt-1 font-serif text-2xl font-semibold">最近的作品</h2></div><Link href="/novels" className="flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground">全部书架 <ArrowRight className="size-4" /></Link></div>
          <div className="paper-surface overflow-hidden rounded-3xl">
            {recentNovels.length > 0 ? recentNovels.map((novel, index) => (
              <Link key={novel.id} href={`/novels/${novel.id}`} className="group flex items-center gap-4 border-b border-border/70 p-4 transition-colors last:border-0 hover:bg-white/65 sm:p-5">
                <div className="grid size-12 shrink-0 place-items-center rounded-2xl bg-secondary font-serif text-lg font-semibold text-primary">{String(index + 1).padStart(2, "0")}</div>
                <div className="min-w-0 flex-1"><p className="truncate font-medium">{novel.title}</p><p className="mt-1 text-xs text-muted-foreground">{novel.author || "未知作者"} · {novel.chapter_count} 章</p></div>
                <ArrowRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-1" />
              </Link>
            )) : <div className="p-8 text-center"><BookOpenText className="mx-auto size-8 text-muted-foreground" /><p className="mt-3 font-medium">书架还没有作品</p><p className="mt-1 text-sm text-muted-foreground">导入 TXT 后，这里会显示最近阅读的故事。</p></div>}
          </div>
        </div>

        <div>
          <div className="mb-4"><p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary">Quick actions</p><h2 className="mt-1 font-serif text-2xl font-semibold">开始一项工作</h2></div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
            {quickActions.map((action) => (
              <Link key={action.title} href={action.href} className="paper-surface group flex items-center gap-4 rounded-2xl p-4 transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-lg">
                <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-secondary text-foreground transition-colors group-hover:bg-primary group-hover:text-primary-foreground"><action.icon className="size-[18px]" /></span>
                <span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{action.title}</span><span className="mt-0.5 block truncate text-xs text-muted-foreground">{action.description}</span></span>
                <ArrowRight className="size-4 text-muted-foreground" />
              </Link>
            ))}
          </div>
        </div>
      </section>
    </PageContainer>
  );
}
