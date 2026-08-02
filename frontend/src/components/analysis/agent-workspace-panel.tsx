"use client";

/**
 * Phase 25.2-04 — Agent Workspace 面板（D-19）。
 *
 * /analysis 第三个 "agent" tab：向 agent-service 发起 SSE 流式技能运行
 * （fetch-stream，非 EventSource —— 需要 Bearer 头），增量渲染回答、工具调用摘要、
 * 候选产物预览与 owner 审批（approve/reject 只走 /api/agent/artifacts 状态机）。
 *
 * 流契约（25.2-05）：POST /agent/novels/{novel_id}/runs（经 next.config rewrite
 * 到 agent-service :3100），帧序 delta → tool_start/tool_end → turn_end →
 * artifact → run_end(completed|cancelled|failed)。
 *
 * 复用而非重建：MessageBubble / CitationChip（reader-chat-panel），
 * handleCitationNavigate（analysis-chat-panel），job-status bar 语义
 * （analysis-chat-panel）。审批确认用 ui/dialog（禁用浏览器原生确认弹窗）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Check,
  LoaderCircle,
  RotateCcw,
  Send,
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
import {
  ArtifactPreview,
  type AnalysisChapterRef,
} from "@/components/analysis/analysis-chat-panel";
import {
  CitationChip,
  MessageBubble,
  type CitationNavigateTarget,
} from "@/components/reader/reader-chat-panel";
import type { StructureNodeSelection } from "@/components/structure/structure-types";
import {
  ApprovalRequestDialog,
  type ApprovalRequestView,
} from "@/components/analysis/approval-request-dialog";
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
type ToolCallState = {
  toolName: string;
  status: "running" | "done" | "error";
};

type Props = {
  novelId: string;
  /** 章节 id/章号映射（与 AnalysisChatPanel 同源，供剧透边界提示）。 */
  chapters: AnalysisChapterRef[];
  /** 该小说「显示全书」开关状态（服务端持久化偏好）。 */
  fullBook: boolean;
  /** 服务端阅读进度 chapter_id（DB id），无进度为 null。 */
  progressChapterId: number | null;
  /** 当前结构选中范围。 */
  selection: StructureNodeSelection | null;
  className?: string;
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

export function AgentWorkspacePanel({
  novelId,
  chapters,
  fullBook,
  progressChapterId,
  selection,
  className,
}: Props) {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [lastQuestion, setLastQuestion] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [toolCalls, setToolCalls] = useState<ToolCallState[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [runStatus, setRunStatus] = useState<SkillRunStatus | null>(null);
  const [artifact, setArtifact] = useState<ArtifactView | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [acting, setActing] = useState(false);
  // 25.3-06：待决 Web ApprovalRequest（approval_request SSE 帧驱动；决策回 FastAPI）。
  const [approvalRequest, setApprovalRequest] = useState<ApprovalRequestView | null>(null);
  const [approvalOpen, setApprovalOpen] = useState(false);

  /** 流控制器：取消时 abort，agent-service 收到断开即服务端取消。 */
  const abortRef = useRef<AbortController | null>(null);
  /** runId 镜像 ref（异步 handler 中读最新值，避免闭包陈旧）。 */
  const runIdRef = useRef<number | null>(null);

  // 换书时同步重置（render 期重置，不 unmount —— 保住 tab 切换的其余状态）。
  const [loadedNovel, setLoadedNovel] = useState<string | null>(null);
  if (loadedNovel !== novelId) {
    setLoadedNovel(novelId);
    setQuestion("");
    setLastQuestion(null);
    setAnswer("");
    setToolCalls([]);
    setRunId(null);
    runIdRef.current = null;
    setRunStatus(null);
    setArtifact(null);
    setError(null);
    setApprovalRequest(null);
    setApprovalOpen(false);
    abortRef.current?.abort();
    abortRef.current = null;
  }

  const busy = runStatus !== null && !RUN_TERMINAL.has(runStatus);

  // ---------- 会话恢复：mount 时重灌最新 run + artifact ----------

  const refreshLatestArtifact = useCallback(async () => {
    try {
      const art = await agentApi.getLatestArtifact(novelId);
      if (!art) return;
      setArtifact(art);
      // 恢复场景没有流式 delta：用产物正文回填回答（引证芯片由 ArtifactPreview 渲染）。
      const text = extractAnswerText(art);
      if (text) setAnswer((prev) => prev || text);
      // 元数据视图不带正文：尽力读最新修订正文（引证芯片数据源）。
      if (!art.content) {
        const content = await agentApi.getArtifactContent(novelId, art.id);
        setArtifact({ ...art, content: content ?? undefined });
        const fullText = extractAnswerText({ ...art, content: content ?? undefined });
        if (fullText) setAnswer((prev) => prev || fullText);
      }
    } catch {
      // 产物读取失败仅影响预览区，不阻断面板。
    }
  }, [novelId]);

  useEffect(() => {
    if (!novelId) return;
    let cancelled = false;
    setRestoring(true);
    void (async () => {
      try {
        const run = await agentApi.getLatestRun(novelId);
        if (cancelled || !run) return;
        runIdRef.current = run.id;
        setRunId(run.id);
        setRunStatus(run.status);
        if (run.status === "completed") {
          await refreshLatestArtifact();
        }
      } catch {
        // 恢复失败保持空态。
      } finally {
        if (!cancelled) setRestoring(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [novelId, refreshLatestArtifact]);

  // ---------- 流帧处理 ----------

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
          await refreshLatestArtifact();
        }
      } catch {
        // 读失败不影响流。
      }
    },
    [novelId, refreshLatestArtifact]
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
          // 只通知：渲染对话框等待 owner 决策；服务端仍在短轮询（决策权威在 FastAPI）。
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
          if (status === "completed") void refreshLatestArtifact();
          break;
        }
        case "turn_end":
          // 用量帧仅作记录，无 UI。
          break;
      }
    },
    [refreshLatestArtifact, resolveArtifact]
  );

  // ---------- 交互：发送 / 取消 / 重试 / 审批 ----------

  const handleSend = async (override?: string) => {
    const q = (override ?? question).trim();
    if (!q) {
      setError("请输入问题");
      return;
    }
    setError(null);
    setQuestion("");
    setLastQuestion(q);
    setAnswer("");
    setToolCalls([]);
    setArtifact(null);
    runIdRef.current = null;
    setRunId(null);
    setRunStatus("running");

    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    void streamAgentRun(
      `/agent/novels/${novelId}/runs`,
      { question: q, skill: "answer-reading-question", input: {}, branch: null },
      {
        signal: ac.signal,
        onEvent: handleFrame,
        onError: () => setError("收到异常流帧，已忽略该帧"),
      }
    ).catch((err: unknown) => {
      if ((err as Error)?.name === "AbortError") {
        setRunStatus("cancelled");
        return;
      }
      setError("运行失败，请稍后重试");
      setRunStatus("failed");
    });

    // 尽力从服务端发现 runId（取消端点兜底与状态回显用）。
    // agent-service 在写 SSE 头前已创建 run，读回最新 run 即本次运行。
    void (async () => {
      try {
        const run = await agentApi.getLatestRun(novelId);
        if (run && !ac.signal.aborted) {
          runIdRef.current = run.id;
          setRunId(run.id);
          if (run.status !== "queued" && run.status !== "running") {
            setRunStatus(run.status);
          }
        }
      } catch {
        // 发现失败仅影响取消端点兜底，忽略。
      }
    })();
  };

  const handleCancel = async () => {
    abortRef.current?.abort();
    setRunStatus("cancelled");
    // 双保险：能拿到 runId 就显式调取消端点；agent-service 断开即取消兜底。
    const id = runIdRef.current;
    if (id != null) {
      try {
        await agentApi.cancelRun(novelId, id);
      } catch {
        // 取消端点失败不阻断（断开即取消已兜底）。
      }
    }
  };

  const handleRetry = () => {
    if (lastQuestion) void handleSend(lastQuestion);
  };

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

  /** citation → 阅读页对应章节（与 analysis-chat-panel 同款跳转）。 */
  const handleCitationNavigate = (target: CitationNavigateTarget) => {
    const params = new URLSearchParams();
    params.set("chapter", String(target.chapter_id));
    params.set("start", String(target.source_start));
    params.set("from", "timeline");
    router.push(`/novels/${novelId}?${params.toString()}`);
  };

  // ---------- 派生渲染数据 ----------

  const cutoffChapterNumber = useMemo(() => {
    const byProgress =
      progressChapterId != null
        ? chapters.find((c) => c.id === progressChapterId)?.chapter_number ?? null
        : null;
    if (byProgress != null) return byProgress;
    if (!chapters.length) return null;
    return chapters.reduce(
      (min, c) => Math.min(min, c.chapter_number),
      Number.POSITIVE_INFINITY
    );
  }, [chapters, progressChapterId]);

  const boundaryLabel = fullBook
    ? "全书模式"
    : `基于你已读至第 ${cutoffChapterNumber ?? 1} 章`;

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
      data-testid="agent-workspace-panel"
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-2xl border border-border/60 bg-card text-sm motion-transition-content",
        className
      )}
    >
      {/* 消息/状态流 */}
      <div
        data-testid="agent-answer"
        className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3"
      >
        {restoring ? (
          <p className="text-center text-xs text-muted-foreground">恢复工作区…</p>
        ) : null}
        {!restoring && !answer && !busy && !runStatus ? (
          <div
            data-testid="agent-empty"
            className="grid min-h-24 place-items-center text-center text-xs text-muted-foreground"
          >
            <p>
              向智能体提问。回答流式生成，引用可跳转原文；
              候选产物需你显式批准后才算发布。
            </p>
          </div>
        ) : null}
        {lastQuestion ? (
          <div
            data-testid="agent-user-question"
            className="ml-6 rounded-xl bg-primary/10 px-3 py-2"
          >
            <p className="whitespace-pre-wrap text-[13px] leading-relaxed">
              {lastQuestion}
            </p>
          </div>
        ) : null}
        {answerMessage ? (
          <MessageBubble
            message={answerMessage}
            onCitationNavigate={handleCitationNavigate}
          />
        ) : null}

        {/* 工具调用摘要条：tool_start / tool_end 帧 */}
        {toolCalls.length > 0 ? (
          <div
            data-testid="agent-tool-summary"
            className="flex flex-wrap gap-1.5 rounded-lg border border-border/60 bg-muted/40 px-3 py-2"
          >
            {toolCalls.map((t, i) => (
              <span
                key={`${t.toolName}-${i}`}
                data-testid="agent-tool-call"
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

        {/* job-status bar（分析对话面板同款语义） */}
        {runStatus ? (
          <div
            data-testid="agent-job-status"
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
                    data-testid="agent-cancel"
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
                    data-testid="agent-retry"
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
            data-testid="agent-artifact-preview"
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
                  data-testid="agent-artifact-status"
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
                          data-testid="agent-approve"
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
                          data-testid="agent-approve-confirm"
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
                          data-testid="agent-reject"
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
                          data-testid="agent-reject-confirm"
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

            {/* 25.3-05：产物正文经类型键渲染器注册表解析（pi-web-ui 模式借用，零 import）。 */}
            <div className="mt-2 border-t border-primary/10 pt-2">
              <ArtifactPreview
                artifact={artifact}
                novelId={novelId}
                onCitationNavigate={handleCitationNavigate}
              />
            </div>
          </div>
        ) : null}
      </div>

      {/* 上下文提示：剧透边界（与服务端同一偏好源） */}
      <div
        data-testid="agent-context"
        className="shrink-0 border-t border-border/50 bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground"
      >
        <p>
          智能体工作区 · <span data-testid="agent-boundary">{boundaryLabel}</span>
          {selection ? (
            <span className="ml-1">· 结构范围 {selection.label}</span>
          ) : null}
        </p>
      </div>

      {error ? (
        <div
          data-testid="agent-error"
          className="shrink-0 px-3 py-1 text-xs text-destructive"
          role="alert"
        >
          {error}
        </div>
      ) : null}

      {/* 问题输入 */}
      <div className="flex shrink-0 items-end gap-2 border-t border-border/70 p-2">
        <textarea
          data-testid="agent-input"
          className="min-h-[2.5rem] max-h-24 min-w-0 flex-1 resize-y rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          placeholder={busy ? "运行中，请稍候…" : "向智能体提问（基于全书/当前阅读进度）…"}
          value={question}
          disabled={busy}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void handleSend();
            }
          }}
          aria-label="智能体问题输入"
        />
        <Button
          type="button"
          size="sm"
          data-testid="agent-send"
          disabled={busy || !question.trim()}
          onClick={() => void handleSend()}
        >
          {busy ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
        </Button>
      </div>

      {/* 25.3-06 Web Approval 对话框（决策 POST 回 FastAPI；仅渲染，不做本地判定） */}
      <ApprovalRequestDialog
        open={approvalOpen}
        onOpenChange={setApprovalOpen}
        request={approvalRequest}
        onDecide={handleApprovalDecided}
        onError={(message) => setError(message)}
      />
    </div>
  );
}
