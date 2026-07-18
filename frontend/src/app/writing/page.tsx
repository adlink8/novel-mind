"use client";

import Link from "next/link";
import { ArrowRight, BookOpenText, Feather, GitBranch, Sparkles } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/page-header";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const steps = [
  { number: "01", title: "选择原作", description: "从书架选择已经建立章节与语义索引的小说。", icon: BookOpenText },
  { number: "02", title: "确定分支", description: "锁定一个事件、人物选择或未完成的叙事可能。", icon: GitBranch },
  { number: "03", title: "协作写作", description: "让 AI 引用原文记忆，同时保留你的叙事决定权。", icon: Sparkles },
];

export default function WritingPage() {
  return (
    <PageContainer className="space-y-8">
      <PageHeader eyebrow="Writing studio" title="创作中心" description="从可靠的原作记忆出发，规划故事分支、建立草稿并与 AI 协作续写。" />
      <section className="relative overflow-hidden rounded-[32px] bg-foreground p-7 text-white shadow-[0_30px_80px_-42px_rgba(25,35,31,0.7)] sm:p-10">
        <div className="absolute right-[-3rem] top-[-4rem] size-56 rounded-full border border-white/10" />
        <div className="absolute bottom-[-5rem] right-24 size-44 rounded-full bg-white/[0.04]" />
        <div className="relative grid gap-8 lg:grid-cols-[1fr_auto] lg:items-end">
          <div className="max-w-2xl">
            <span className="mb-5 grid size-12 place-items-center rounded-2xl bg-white/10"><Feather className="size-5 text-primary" /></span>
            <h2 className="font-serif text-3xl font-semibold tracking-tight sm:text-4xl">不是让 AI 替你写，<br />而是让故事记得更多。</h2>
            <p className="mt-4 max-w-xl text-sm leading-7 text-white/60">创作能力仍在建设中。当前可以先完成原作导入与检索评测，为后续分支创作建立可信上下文。</p>
          </div>
          <Link href="/novels" className={cn(buttonVariants({ size: "lg" }), "h-11 rounded-full bg-primary px-6 text-white hover:bg-primary/90")}>前往书架 <ArrowRight className="ml-2 size-4" /></Link>
        </div>
      </section>
      <section>
        <div className="mb-5"><p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary">Workflow</p><h2 className="mt-1 font-serif text-2xl font-semibold">创作路径</h2></div>
        <div className="grid gap-4 md:grid-cols-3">
          {steps.map((step) => (
            <article key={step.number} className="paper-surface rounded-3xl p-6">
              <div className="flex items-center justify-between"><span className="text-xs font-semibold tracking-[0.18em] text-muted-foreground">{step.number}</span><span className="grid size-10 place-items-center rounded-xl bg-secondary text-primary"><step.icon className="size-[18px]" /></span></div>
              <h3 className="mt-8 font-serif text-xl font-semibold">{step.title}</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">{step.description}</p>
            </article>
          ))}
        </div>
      </section>
      <section className="paper-surface flex flex-col items-start justify-between gap-5 rounded-3xl p-6 sm:flex-row sm:items-center sm:p-8">
        <div><p className="font-serif text-xl font-semibold">草稿区将在创作管线接入后开放</p><p className="mt-1 text-sm text-muted-foreground">当前不会展示虚假样例或不可执行的按钮。</p></div>
        <span className="rounded-full border border-border bg-secondary px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">Planned</span>
      </section>
    </PageContainer>
  );
}
