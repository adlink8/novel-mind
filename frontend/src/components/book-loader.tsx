"use client";

/**
 * 小书本翻页加载动画 — 进入书本/章节加载时的等待指示。
 * 替代通用 spinner：一本摊开的小书循环翻页，贴合产品「书」的主题。
 */

import { cn } from "@/lib/utils";
import styles from "./book-loader.module.css";

type Props = {
  /** 加载文案，缺省不显示文字 */
  label?: string;
  className?: string;
};

export function BookLoader({ label, className }: Props) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("flex flex-col items-center justify-center gap-4", className)}
    >
      <div className="relative">
        <div aria-hidden className={styles.glow} />
        <div aria-hidden className={styles.book}>
          <div className={styles.baseLeft} />
          <div className={styles.baseRight} />
          <div className={styles.spine} />
          {/* 三片书叶错相位循环翻动 */}
          <div className={styles.leaf} style={{ "--leaf-delay": "0s" } as React.CSSProperties} />
          <div className={styles.leaf} style={{ "--leaf-delay": "-0.73s" } as React.CSSProperties} />
          <div className={styles.leaf} style={{ "--leaf-delay": "-1.46s" } as React.CSSProperties} />
        </div>
      </div>
      {label ? <p className="text-sm text-muted-foreground">{label}</p> : null}
    </div>
  );
}
