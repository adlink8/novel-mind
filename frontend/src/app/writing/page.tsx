/**
 * 创作中心 - app/writing/page.tsx
 * 占位页：真实创作流程（fanfictionApi）留待后续里程碑接入。
 * 视觉为一本摊开的 3D 书：封面内侧是宣言，三张书页即三步创作路径；
 * 草稿区以稿纸 + 朱砂「候」印章标记 Planned，不放虚假可点功能。
 */

import Link from "next/link";
import { ArrowRight, BookOpenText, Feather, GitBranch, Sparkles } from "lucide-react";

import { ChapterOrnament } from "@/components/chapter-ornament";
import { FlipBook, type FlipBookPage } from "@/components/flip-book";
import { PageContainer, PageHeader } from "@/components/page-header";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const steps = [
  {
    id: "choose",
    numeral: "壹",
    title: "选择原作",
    description: "从书架选择已经建立章节与语义索引的小说。",
    icon: BookOpenText,
  },
  {
    id: "branch",
    numeral: "贰",
    title: "确定分支",
    description: "锁定一个事件、人物选择或未完成的叙事可能。",
    icon: GitBranch,
  },
  {
    id: "cowrite",
    numeral: "叁",
    title: "协作写作",
    description: "让 AI 引用原文记忆，同时保留你的叙事决定权。",
    icon: Sparkles,
  },
];

/** 三步创作路径 = 三张书页（说明卡的书页形态） */
const stepPages: FlipBookPage[] = steps.map((step, index) => ({
  id: step.id,
  front: (
    <div className="flex h-full flex-col p-6 pr-12 xl:p-8 xl:pr-14">
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-primary">
        创作路径 · Step {index + 1}
      </p>
      <div className="mt-6 flex-1">
        <span className="grid size-11 place-items-center rounded-xl bg-secondary text-primary">
          <step.icon className="size-5" />
        </span>
        <h3 className="mt-5 font-serif text-2xl font-semibold">{step.title}</h3>
        <p className="mt-3 max-w-xs text-sm leading-7 text-muted-foreground">
          {step.description}
        </p>
      </div>
      <p className="text-right text-xs text-muted-foreground/60">{step.numeral}</p>
    </div>
  ),
}));

function HeroInsideCover({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className={cn(
        "flex h-full flex-col justify-center",
        compact ? "p-7" : "p-10 xl:p-12"
      )}
    >
      <div
        className={cn(
          "inline-flex w-fit items-center gap-2 rounded-full border border-primary/25 bg-primary/10 tracking-wide text-primary",
          compact ? "mb-5 px-3 py-1 text-[11px]" : "mb-6 px-3 py-1.5 text-xs"
        )}
      >
        <Feather className={compact ? "size-3" : "size-3.5"} />
        创作中心 · 建设中
      </div>
      <h1
        className={cn(
          "font-serif font-semibold tracking-[-0.02em] text-foreground",
          compact
            ? "text-[27px] leading-[1.2]"
            : "text-4xl leading-[1.15] tracking-[-0.025em] xl:text-[2.7rem]"
        )}
      >
        不是让 AI 替你写，
        <br />
        而是让故事记得更多。
      </h1>
      <p
        className={cn(
          "max-w-md text-muted-foreground",
          compact ? "mt-3 text-[13px] leading-6" : "mt-4 text-sm leading-7"
        )}
      >
        创作能力仍在建设中。当前可以先完成原作导入与检索评测，为后续分支创作建立可信上下文。
      </p>
      <div className={compact ? "mt-6" : "mt-7"}>
        <Link
          href="/novels"
          className={cn(
            buttonVariants({ size: "lg" }),
            "rounded-full bg-primary text-white hover:bg-primary/90",
            compact ? "h-10 w-fit px-5" : "h-11 px-6"
          )}
        >
          前往书架 <ArrowRight className="ml-2 size-4" />
        </Link>
      </div>
      <p
        className={cn(
          "text-muted-foreground/70",
          compact ? "mt-6 text-[11px]" : "mt-8 text-xs"
        )}
      >
        点右页边缘翻开创作路径
      </p>
    </div>
  );
}

const insideBackCover = (
  <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
    <span className="font-serif text-2xl text-primary">❦</span>
    <p className="font-serif text-xl font-semibold text-foreground">
      每一个分支，都从原作的一页开始。
    </p>
    <p className="max-w-xs text-sm leading-6 text-muted-foreground">
      草稿与分支管理将在创作管线接入后写进这本书。
    </p>
  </div>
);

export default function WritingPage() {
  return (
    <PageContainer className="space-y-8">
      <PageHeader
        eyebrow="Writing studio"
        title="创作中心"
        description="从可靠的原作记忆出发，规划故事分支、建立草稿并与 AI 协作续写。"
      />

      {/* ── Hero：摊开的创作之书（平板/桌面对开 / 窄屏单页竖版） ── */}
      <div className="hidden md:block">
        <FlipBook
          pages={stepPages}
          tone="on-light"
          ambient
          ariaLabel="创作中心导览书"
          className="mx-auto w-full max-w-[1080px]"
          insideCover={<HeroInsideCover />}
          insideBackCover={insideBackCover}
        />
      </div>
      <div className="flex justify-center md:hidden">
        <FlipBook
          layout="single"
          pages={stepPages}
          tone="on-light"
          ambient
          ariaLabel="创作中心导览书"
          className="w-full max-w-[430px]"
          insideCover={<HeroInsideCover compact />}
          insideBackCover={insideBackCover}
        />
      </div>

      <ChapterOrnament />

      {/* ── 草稿区：一张待写的稿纸，朱砂「候」印章标记 Planned ── */}
      <section className="paper-surface relative overflow-hidden rounded-3xl p-6 sm:p-8">
        {/* 稿纸横线 */}
        <div aria-hidden className="pointer-events-none absolute inset-x-6 inset-y-5 space-y-[26px] sm:inset-x-8">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-px w-full bg-border/50" />
          ))}
        </div>
        <div className="relative flex flex-col items-start justify-between gap-5 sm:flex-row sm:items-center">
          <div>
            <p className="font-serif text-xl font-semibold">
              草稿区将在创作管线接入后开放
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              当前不会展示虚假样例或不可执行的按钮。
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span
              aria-hidden
              className="grid size-11 rotate-3 place-items-center rounded-md border border-[#b03a2e]/50 bg-[#b03a2e]/90 font-serif text-lg font-semibold text-white shadow-sm"
            >
              候
            </span>
            <span className="rounded-full border border-border bg-secondary px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              Planned
            </span>
          </div>
        </div>
      </section>
    </PageContainer>
  );
}
