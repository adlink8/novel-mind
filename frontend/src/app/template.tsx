import type { ReactNode } from "react";

/**
 * 路由段模板 — 每次导航重新挂载，触发一次 token 驱动的淡入上移。
 * h-full 保持 /analysis 全视口工作台的尺寸链；reduced-motion 下动画被全局覆盖。
 */
export default function Template({ children }: { children: ReactNode }) {
  return <div className="motion-enter h-full">{children}</div>;
}
