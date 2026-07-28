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

import React, { type ReactNode } from "react";
import { Inbox } from "lucide-react";
import { Button } from "@/components/ui/button";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;           // 主标题
  description?: string;    // 描述文字
  actionLabel?: string;    // 操作按钮文字
  onAction?: () => void;   // 操作按钮点击回调
}

export function EmptyState({
  icon = <Inbox className="size-6" />,
  title,
  description,
  actionLabel,
  onAction,
}: EmptyStateProps) {
  return (
    <div
      className="paper-surface flex flex-col items-center justify-center rounded-3xl border-dashed p-10 text-center motion-transition-content sm:p-14"
      role="status"
    >
      <div className="mb-5 grid size-14 place-items-center rounded-2xl bg-secondary text-primary">{icon}</div>
      <h3 className="font-serif text-xl font-semibold mb-2">{title}</h3>
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
