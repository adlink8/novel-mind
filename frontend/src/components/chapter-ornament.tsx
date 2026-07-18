import { cn } from "@/lib/utils";

/**
 * 章节分隔饰样 — 传统章回小说中的「花押」分隔线。
 * 用于工作台页面的主要区块之间，强化纸面书卷感。
 */
export function ChapterOrnament({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn("flex items-center gap-3 text-muted-foreground/50", className)}
    >
      <span className="h-px flex-1 bg-border/70" />
      <span className="font-serif text-xs tracking-[0.3em]">❦</span>
      <span className="h-px flex-1 bg-border/70" />
    </div>
  );
}
