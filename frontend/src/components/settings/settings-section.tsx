import type { ReactNode } from "react";

/**
 * 设置中心章节包装 — 章回体小节标题（朱砂章节字 + 衬线标题）。
 * 各设置区块（账户/路由/模型/用量）共用的节骨架。
 */
export function SettingsSection({
  chapter,
  title,
  action,
  children,
}: {
  /** 章节字（壹/贰/叁/肆…），渲染为朱砂印章式标记 */
  chapter: string;
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="motion-transition-content">
      <div className="mb-4 flex items-center gap-3">
        <span
          aria-hidden
          className="grid size-8 shrink-0 rotate-2 place-items-center rounded-md border border-[#b03a2e]/40 bg-[#b03a2e]/10 font-serif text-sm font-semibold text-[#b03a2e]"
        >
          {chapter}
        </span>
        <h3 className="font-serif text-xl font-semibold">{title}</h3>
        {action ? <div className="ml-auto">{action}</div> : null}
      </div>
      {children}
    </section>
  );
}
