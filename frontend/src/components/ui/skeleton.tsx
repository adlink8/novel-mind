import { cn } from "@/lib/utils"

/**
 * 骨架屏占位块 — 加载期间替代真实内容形状。
 * animate-pulse 仅用于加载占位（motion contract 允许的 loading 场景）；
 * 本组件不在 Phase 18 契约文件清单内，脉冲动画封装在此处统一复用。
 */
function Skeleton({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  )
}

export { Skeleton }
