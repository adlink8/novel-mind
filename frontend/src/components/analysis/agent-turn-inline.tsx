"use client";

/**
 * 统一对话/侧边栏共用的内联智能体回合（AI 自动路由）。
 *
 * 一个自包含的 SSE 流式智能体回合：挂载即发起 run（skill **缺省**，
 * 由后端自动路由；`skill` prop 仅为内部高级覆盖，UI 永不暴露），
 * 增量渲染 delta 回答、工具调用摘要、候选产物预览 + 审批
 * （approve/reject 只走 /api/agent/artifacts 状态机）与 Web
 * ApprovalRequest（决策权威在 FastAPI，本组件只渲染并 POST 人的选择）。
 *
 * 流契约（25.2-05 + 25.3-06）：POST /agent/novels/{novel_id}/runs
 * 帧序 delta → tool_start/tool_end → turn_end → artifact →
 * approval_request → run_end(completed|cancelled|failed)。
 *
 * 复用而非重建：MessageBubble / CitationChip（reader-chat-panel）、
 * ArtifactPreview（analysis-chat-panel re-export）、ApprovalRequestDialog。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  LoaderCircle,
  RotateCcw,
  Sparkles,
  X,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { ArtifactPreview } from "@/components/analysis/analysis-chat-panel";
import {
  ApprovalRequestDialog,
  type ApprovalRequestView,
} from "@/components/analysis/approval-request-dialog";
import {
  MessageBubble,
  type CitationNavigateTarget,
} from "@/components/reader/reader-chat-panel";
import {
  agentApi,
  type ArtifactStatus,
  type ArtifactView,
  type MessageView,
  type SkillRunStatus,
} from "@/lib/api";
import { streamAgentRun, type AgentRunFrame } from "@/lib/sse";
import { cn } from "@/lib/utils";

/** 工具调用摘要条目（tool_start 开、tool_end 闭）。 */
export type AgentToolCallState = {
  toolName: string;
  status: "running" | "done" | "error";
};

/** 一个已触发的智能体回合（挂载后 AgentTurnInline 自动发起 SSE run）。 */
export type AgentTurnItem = {
  id: number;
  question: string;
  /** 内部高级覆盖（非用户选择）；缺省 = 后端自动路由。 */
  skill?: string;
  input?: Record<string, unknown>;
};

const RUN_STATUS_LABELS: Record<SkillRunStatus, string> = {
  queued: "排队中",
  running: "回答中",
  cancelled: "已取消",
  completed: "已完成",
  failed: "运行失败",
};

const RUN_TERMINAL: ReadonlySet<SkillRunStatus> = new Set([
  "cancelled",
  "completed",
  "failed",
]);

const ARTIFACT_STATUS_LABELS: Record<ArtifactStatus, string> = {
  candidate: "候选",
  validated: "已校验",
  approved: "已批准",
  published: "已发布",
  rejected: "已拒绝",
};

/** 产物正文答案文本（session restore 时回填回答；无则空串）。 */
function extractAnswerText(artifact: ArtifactView | null): string {
  const blocks = artifact?.content?.answer?.answer_blocks ?? [];
  return blocks
    .map((b) => b.text ?? "")
    .filter((t) => t.length > 0)
    .join("\n");
}

type Props = {
  novelId: string;
  /** 触发本回合的用户消息（展示在回合上方；空则只渲染 assistant 侧）。 */
  initialQuestion: string;
  /**
   * 内部高级覆盖（非用户选择）：缺省 = 后端自动路由。
   * 仅在极少数入口（如明确的「生成插图」动作按钮）在路由未就绪时使用。
   */
  skill?: string;
  /** 上下文锚（chapter_range / selection 等）；最终 scope 由服务端强制。 */
  input?: Record<string, unknown>;
  onCitationNavigate: (target: CitationNavigateTarget) => void;
  /** run 达到终态（completed/cancelled/failed）后回调。 */
  onDone?: () => void;
  onError?: (message: string) => void;
  className?: string;
};

export function AgentTurnInline({
  novelId,
  initialQuestion,
  skill,
  input,
  onCitationNavigate,
  onDone,
  onError,
  className,
}: Props) {
  const [answer, setAnswer] = useState("");
  const [toolCalls, setToolCalls] = useState<AgentToolCallState[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [runStatus, setRunStatus] = useState<SkillRunStatus | null>(null);
  const [artifact, setArtifact] = useState<ArtifactView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approvalRequest, setApprovalRequest] =
    useState<ApprovalRequestView | null>(null);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [acting, setActing] = useState(false);

  /** 流控制器：取消时 abort，agent-service 收到断开即服务端取消。 */
  const abortRef = useRef<AbortController | null>(null);
  /** runId 镜像 ref（异步 handler 中读最新值，避免闭包陈旧）。 */
  const runIdRef = useRef<number | null>(null);
  const startedRef = useRef(false);

  const busy = runStatus !== null && !RUN_TERMINAL.has(runStatus);

  const resolveArtifact = useCallback(
    async (maybe: Record<string, unknown> | undefined) => {
      try {
        if (maybe && typeof maybe.id === "number") {
          setArtifact(maybe as unknown as ArtifactView);
        } else if (maybe && typeof maybe.artifact_id === "number") {
          const art = await agentApi.getArtifact(
            novelId,
            maybe.artifact_id as number
          );
          setArtifact(art);
        } else {
          const art = await agentApi.getLatestArtifact(novelId);
          if (art) setArtifact(art);
        }
      } catch {
        // 读失败不影响流。
      }
    },
    [novelId]
  );

  const notifyDone = useCallback(
    (status: SkillRunStatus) => {
      if (RUN_TERMINAL.has(status)) onDone?.();
    },
    [onDone]
  );

  const handleFrame = useCallback(
    (frame: AgentRunFrame) => {
      switch (frame.type) {
        case "delta":
          setAnswer((prev) => prev + (typeof frame.text === "string" ? frame.text : ""));
          break;
        case "tool_start":
          setToolCalls((prev) => [
            ...prev,
            {
              toolName: typeof frame.toolName === "string" ? frame.toolName : "工具",
              status: "running",
            },
          ]);
          break;
        case "tool_end": {
          const name = typeof frame.toolName === "string" ? frame.toolName : null;
          setToolCalls((prev) =>
            prev.map((t, i) =>
              name !== null && t.toolName === name && i === prev.length - 1
                ? { ...t, status: frame.isError ? "error" : "done" }
                : t
            )
          );
          break;
        }
        case "artifact":
          void resolveArtifact(frame.artifact as Record<string, unknown> | undefined);
          break;
        case "approval_request":
          // 只通知：渲染对话框等待 owner 决策；决策权威在 FastAPI。
          setApprovalRequest(frame.request as ApprovalRequestView);
          setApprovalOpen(true);
          break;
        case "run_end": {
          const id =
            typeof frame.runId === "number" ? frame.runId : Number(frame.runId);
          if (!Number.isNaN(id)) {
            runIdRef.current = id;
            setRunId(id);
          }
          const status =
            frame.status === "cancelled" || frame.status === "failed"
              ? frame.status
              : "completed";
          setRunStatus(status);
          if (status === "completed") {
            void agentApi.getLatestArtifact(novelId).then((art) => {
              if (art) setArtifact(art);
            });
          }
          notifyDone(status);
          break;
        }
        case "turn_end":
          // 用量帧仅作记录，无 UI。
          break;
      }
    },
    [notifyDone, resolveArtifact, novelId]
  );

  const handleCancel = useCallback(async () => {
    abortRef.current?.abort();
    setRunStatus("cancelled");
    notifyDone("cancelled");
    // 双保险：能拿到 runId 就显式调取消端点；agent-service 断开即取消兜底。
    const id = runIdRef.current;
    if (id != null) {
      try {
        await agentApi.cancelRun(novelId, id);
      } catch {
        // 取消端点失败不阻断（断开即取消已兜底）。
      }
    }
  }, [notifyDone, novelId]);

  const startStream = useCallback(() => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setRunStatus("running");
    const body: Record<string, unknown> = {
      question: initialQuestion,
      input: input ?? {},
      branch: null,
    };
    if (skill) body.skill = skill;

    void streamAgentRun(`/agent/novels/${novelId}/runs`, body, {
      signal: ac.signal,
      onEvent: handleFrame,
      onError: () => setError("收到异常流帧，已忽略该帧"),
    }).catch((err: unknown) => {
      if ((err as Error)?.name === "AbortError") {
        setRunStatus("cancelled");
        notifyDone("cancelled");
        return;
      }
      setError("运行失败，请稍后重试");
      setRunStatus("failed");
      notifyDone("failed");
    });

    // 尽力从服务端发现 runId（取消端点兜底与状态回显用）。
    void (async () => {
      try {
        const run = await agentApi.getLatestRun(novelId);
        if (run && !ac.signal.aborted) {
          runIdRef.current = run.id;
          setRunId(run.id);
          if (run.status !== "queued" && run.status !== "running") {
            setRunStatus(run.status);
            notifyDone(run.status);
          }
        }
      } catch {
        // 发现失败仅影响取消端点兜底，忽略。
      }
    })();
  }, [handleFrame, initialQuestion, input, notifyDone, novelId, skill]);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    startStream();
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
    // 只挂载时启动一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRetry = useCallback(() => {
    setError(null);
    setAnswer("");
    setToolCalls([]);
    setArtifact(null);
    startStream();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelId]);

  const handleApprove = async () => {
    if (!artifact) return;
    setActing(true);
    try {
      const updated = await agentApi.approveArtifact(artifact.id);
      setArtifact(updated);
    } catch {
      setError("批准失败，请重试");
    } finally {
      setActing(false);
      setApproveOpen(false);
    }
  };

  const handleReject = async () => {
    if (!artifact) return;
    setActing(true);
    try {
      const updated = await agentApi.rejectArtifact(artifact.id);
      setArtifact(updated);
    } catch {
      setError("拒绝失败，请重试");
    } finally {
      setActing(false);
      setRejectOpen(false);
    }
  };

  /** 审批决策成功：关闭对话框（服务端轮询会自动继续放行/阻止该动作）。 */
  const handleApprovalDecided = () => {
    setApprovalOpen(false);
  };

  /** 流式回答经 MessageBubble 增量渲染（合成 MessageView，仅展示不落库）。 */
  const answerMessage = useMemo<MessageView | null>(() => {
    if (!answer) return null;
    return {
      id: 0,
      conversation_id: 0,
      sequence: 0,
      role: "assistant",
      body: answer,
      client_message_id: null,
      reply_to_message_id: null,
      selection: null,
      citations: [],
      generation_job: null,
      created_at: "",
    };
  }, [answer]);

  const showApproval =
    artifact !== null &&
    artifact.status !== "published" &&
    artifact.status !== "rejected";

  return (
    <div
      data-testid="agent-turn-inline"
      className={cn(
        "space-y-2 rounded-xl border border-primary/20 bg-card/60 px-3 py-2 text-sm motion-transition-content",
        className
      )}
    >
      {initialQuestion.trim() ? (
        <div
          data-testid="agent-turn-question"
          className="rounded-lg bg-primary/10 px-2 py-1.5"
        >
          <p className="whitespace-pre-wrap text-[13px] leading-relaxed">
            {initialQuestion}
          </p>
        </div>
      ) : null}

      {answerMessage ? (
        <MessageBubble
          message={answerMessage}
          onCitationNavigate={onCitationNavigate}
        />
      ) : null}

      {/* 工具调用摘要条：tool_start / tool_end 帧 */}
      {toolCalls.length > 0 ? (
        <div
          data-testid="agent-turn-tool-summary"
          className="flex flex-wrap gap-1.5 rounded-lg border border-border/60 bg-muted/40 px-3 py-2"
        >
          {toolCalls.map((t, i) => (
            <span
              key={`${t.toolName}-${i}`}
              data-testid="agent-turn-tool-call"
              data-status={t.status}
              className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-background px-2 py-0.5 text-[11px] text-muted-foreground"
            >
              {t.status === "running" ? (
                <LoaderCircle className="size-3 animate-spin" aria-hidden />
              ) : t.status === "done" ? (
                <Check className="size-3 text-emerald-600" aria-hidden />
              ) : (
                <X className="size-3 text-destructive" aria-hidden />
              )}
              {t.toolName}
            </span>
          ))}
        </div>
      ) : null}

      {/* run 状态条 */}
      {runStatus ? (
        <div
          data-testid="agent-turn-job-status"
          data-status={runStatus}
          className="rounded-lg border border-border/60 bg-muted/40 px-3 py-2 text-xs"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5">
              {busy ? (
                <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
              ) : null}
              {RUN_STATUS_LABELS[runStatus]}
            </span>
            <span className="flex gap-1">
              {busy ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid="agent-turn-cancel"
                  aria-label="取消运行"
                  onClick={() => void handleCancel()}
                >
                  <XCircle className="size-3.5" />
                  取消
                </Button>
              ) : null}
              {runStatus === "failed" || runStatus === "cancelled" ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid="agent-turn-retry"
                  aria-label="重试运行"
                  onClick={() => void handleRetry()}
                >
                  <RotateCcw className="size-3.5" />
                  重试
                </Button>
              ) : null}
            </span>
          </div>
        </div>
      ) : null}

      {/* 候选产物预览 + 审批 */}
      {artifact ? (
        <div
          data-testid="agent-turn-artifact-preview"
          className="rounded-lg border border-primary/25 bg-primary/5 px-3 py-2 text-xs"
        >
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="flex items-center gap-1 font-medium text-foreground">
                <Sparkles className="size-3.5 shrink-0 text-primary" />
                候选产物
                <span className="ml-1 text-muted-foreground">
                  {artifact.type}
                  {artifact.schema_version ? ` · ${artifact.schema_version}` : ""}
                </span>
              </p>
              <p
                data-testid="agent-turn-artifact-status"
                data-status={artifact.status}
                className="mt-0.5 text-muted-foreground"
              >
                状态：{ARTIFACT_STATUS_LABELS[artifact.status]}
              </p>
            </div>
            {showApproval ? (
              <div className="flex shrink-0 gap-1">
                <Dialog open={approveOpen} onOpenChange={setApproveOpen}>
                  <DialogTrigger
                    render={
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        data-testid="agent-turn-approve"
                      />
                    }
                  >
                    <Check className="size-3.5" />
                    批准
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>批准产物</DialogTitle>
                      <DialogDescription>
                        批准后产物状态将前进一步（candidate → validated →
                        approved → published），发布需到终态。
                      </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                      <Button
                        type="button"
                        variant="outline"
                        disabled={acting}
                        onClick={() => setApproveOpen(false)}
                      >
                        取消
                      </Button>
                      <Button
                        type="button"
                        data-testid="agent-turn-approve-confirm"
                        disabled={acting}
                        onClick={() => void handleApprove()}
                      >
                        {acting ? (
                          <LoaderCircle className="size-4 animate-spin" />
                        ) : (
                          "确认批准"
                        )}
                      </Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
                <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
                  <DialogTrigger
                    render={
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        data-testid="agent-turn-reject"
                      />
                    }
                  >
                    <X className="size-3.5" />
                    拒绝
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>拒绝产物</DialogTitle>
                      <DialogDescription>
                        拒绝后产物状态变为 rejected，该候选不会再被发布。
                      </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                      <Button
                        type="button"
                        variant="outline"
                        disabled={acting}
                        onClick={() => setRejectOpen(false)}
                      >
                        取消
                      </Button>
                      <Button
                        type="button"
                        variant="destructive"
                        data-testid="agent-turn-reject-confirm"
                        disabled={acting}
                        onClick={() => void handleReject()}
                      >
                        {acting ? (
                          <LoaderCircle className="size-4 animate-spin" />
                        ) : (
                          "确认拒绝"
                        )}
                      </Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
              </div>
            ) : null}
          </div>

          <div className="mt-2 border-t border-primary/10 pt-2">
            <ArtifactPreview
              artifact={artifact}
              novelId={novelId}
              onCitationNavigate={onCitationNavigate}
            />
          </div>
        </div>
      ) : null}

      {error ? (
        <div
          data-testid="agent-turn-error"
          className="shrink-0 px-1 text-xs text-destructive"
          role="alert"
        >
          {error}
        </div>
      ) : null}

      <ApprovalRequestDialog
        open={approvalOpen}
        onOpenChange={setApprovalOpen}
        request={approvalRequest}
        onDecide={handleApprovalDecided}
        onError={(message) => {
          setError(message);
          onError?.(message);
        }}
      />
    </div>
  );
}
