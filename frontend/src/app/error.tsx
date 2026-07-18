"use client";

import { useEffect } from "react";
import { TriangleAlert } from "lucide-react";
import { EmptyState } from "@/components/empty-state";

/** 全局路由错误边界 — 纸面空状态样式，提供重试。 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto w-full max-w-[1480px] px-4 py-16 sm:px-6 xl:px-10">
      <EmptyState
        icon={<TriangleAlert className="size-6" />}
        title="页面出错了"
        description={error.message || "发生未知错误，请重试"}
        actionLabel="重试"
        onAction={reset}
      />
    </div>
  );
}
