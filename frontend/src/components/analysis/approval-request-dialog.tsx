"use client";

/**
 * Phase 25.3-04/06 — Web Approval 对话框（D-11 / REQ-AGENT-07）。
 *
 * 由 agent-service 的 `approval_request` SSE 帧驱动：展示 action 名与
 * payload_summary，三个决策按钮：
 *   - "Approve once"         → POST /api/agent/approval-requests/{id}/confirm {mode:"once"}
 *   - "Approve for this session" → POST .../confirm {mode:"session"}（D-11 会话级批准）
 *   - "Reject"               → POST .../reject
 *
 * 安全边界（T-25.3-04-02/04）：
 *   - 本组件**只渲染并 POST 人的选择**——决策权威在 FastAPI（owner 检查 + 404-hide），
 *     SSE 帧只通知；绝不使用浏览器原生确认弹窗、绝不客户端本地判定。
 *   - 带 getAccessToken() Bearer 认证（api.ts 拦截器自动附加；显式透传以防拦截器缺省）。
 *   - pending 状态下按钮可用；决策中（acting）禁用防重放。
 */

import { useState } from "react";
import { LoaderCircle, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, getAccessToken } from "@/lib/api";

/** ApprovalRequestView 的浏览器侧形状（镜像后端 ApprovalRequestView）。 */
export interface ApprovalRequestView {
  id: number;
  owner_id?: number;
  run_id?: number | null;
  action: string;
  payload_summary?: Record<string, unknown>;
  status: string;
  created_at?: string;
  decided_at?: string | null;
  expires_at?: string | null;
}

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 当前待决的审批请求；null 时对话框空态。 */
  request: ApprovalRequestView | null;
  /** 决策成功回调（父组件据此关闭对话框 / 更新状态）。 */
  onDecide?: (request: ApprovalRequestView) => void;
  /** 决策失败回调。 */
  onError?: (message: string) => void;
};

export function ApprovalRequestDialog({
  open,
  onOpenChange,
  request,
  onDecide,
  onError,
}: Props) {
  const [acting, setActing] = useState(false);

  const decide = async (
    path: "confirm" | "reject",
    body: Record<string, unknown>
  ) => {
    if (!request) return;
    setActing(true);
    try {
      const res = await api.post(
        `/agent/approval-requests/${request.id}/${path}`,
        body,
        {
          headers: getAccessToken()
            ? { Authorization: `Bearer ${getAccessToken()}` }
            : undefined,
        }
      );
      onDecide?.(res.data as ApprovalRequestView);
    } catch {
      onError?.("审批决策失败，请重试");
    } finally {
      setActing(false);
    }
  };

  const handleApproveOnce = () => void decide("confirm", { mode: "once" });
  const handleApproveSession = () => void decide("confirm", { mode: "session" });
  const handleReject = () => void decide("reject", {});

  const actionLabel = request?.action ?? "未知动作";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="approval-request-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-1.5">
            <ShieldAlert className="size-4 shrink-0 text-primary" aria-hidden />
            操作需你确认
          </DialogTitle>
          <DialogDescription>
            智能体请求执行高影响动作，批准后由服务端执行；拒绝则动作被确定性阻止。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 text-sm">
          <div className="flex items-center justify-between gap-2 rounded-lg border border-border/60 bg-muted/40 px-3 py-2">
            <span className="text-muted-foreground">动作</span>
            <span
              data-testid="approval-action"
              className="font-mono text-xs font-medium"
            >
              {actionLabel}
            </span>
          </div>
          {request?.payload_summary ? (
            <div className="rounded-lg border border-border/60 bg-muted/40 px-3 py-2">
              <p className="text-xs text-muted-foreground">摘要</p>
              <p
                data-testid="approval-summary"
                className="mt-1 whitespace-pre-wrap text-[13px]"
              >
                {typeof request.payload_summary.summary === "string"
                  ? request.payload_summary.summary
                  : JSON.stringify(request.payload_summary)}
              </p>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            data-testid="approval-reject"
            disabled={acting || !request}
            onClick={handleReject}
          >
            {acting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            拒绝
          </Button>
          <Button
            type="button"
            variant="outline"
            data-testid="approval-approve-once"
            disabled={acting || !request}
            onClick={handleApproveOnce}
          >
            {acting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            批准一次
          </Button>
          <Button
            type="button"
            data-testid="approval-approve-session"
            disabled={acting || !request}
            onClick={handleApproveSession}
          >
            {acting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            本会话批准
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
