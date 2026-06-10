/**
 * 空状态展示组件
 *
 * 用于列表为空时的友好提示（如书架无小说、无同人文等）。
 * 支持自定义图标、标题、描述和操作按钮。
 *
 * 使用方式:
 *   <EmptyState
 *     icon="📚"
 *     title="书架是空的"
 *     description="导入你的第一本小说"
 *     actionLabel="导入 TXT 文件"
 *     onAction={() => setOpen(true)}
 *   />
 */

"use client";

import React from "react";
import { Button } from "@/components/ui/button";

interface EmptyStateProps {
  icon?: string;           // 显示图标（emoji，默认 📭）
  title: string;           // 主标题
  description?: string;    // 描述文字
  actionLabel?: string;    // 操作按钮文字
  onAction?: () => void;   // 操作按钮点击回调
}

export function EmptyState({
  icon = "📭",
  title,
  description,
  actionLabel,
  onAction,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-muted/30 p-12 text-center">
      <div className="text-5xl mb-4">{icon}</div>
      <h3 className="text-lg font-semibold mb-2">{title}</h3>
      {description && (
        <p className="text-sm text-muted-foreground mb-6 max-w-md">
          {description}
        </p>
      )}
      {actionLabel && onAction && (
        <Button onClick={onAction} size="lg">
          {actionLabel}
        </Button>
      )}
    </div>
  );
}
