"use client";

/**
 * Phase 25.1-02 — 分析页对话面板。
 *
 * 与阅读器聊天（reader-chat-panel）共享同一后端会话/引用底座：
 * `/novels/{id}/conversations` 系列 API（readerChatApi）。本面板不新建后端。
 *
 * 剧透边界与分析页现有规则一致：默认按阅读进度（"基于你已读至第 N 章"）；
 * 全书模式沿用该小说的 timeline_full_book 偏好（后端 reader chat 同读该偏好）。
 *
 * 锚点 = 结构：发送携带 chapter_range 区间锚点（25.1-01 契约）；
 * 服务端按剧透边界收窄 chapter_end 并在 message.anchor 回显实际区间。
 * 起始章超出阅读进度时前端预判禁止发送（服务端同样会 422 chapter_beyond_cutoff）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  LoaderCircle,
  MessageSquarePlus,
  RotateCcw,
  Send,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  MessageBubble,
  type CitationNavigateTarget,
} from "@/components/reader/chat/chat-message-bubble";
import {
  jobStatusLabel,
  newClientMessageId,
} from "@/lib/chat-shared";
import {
  formatChapterRange,
  type StructureNodeSelection,
} from "@/components/structure/structure-types";
import {
  isTerminalJobStatus,
  pollReaderChatJob,
  readerChatApi,
  type ConversationListItem,
  type GenerationJobView,
  type MessageView,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { WorldModelEvidencePanel } from "./world-model-evidence-panel";

/**
 * 25.3-05：Analysis workspace 共享 artifact 预览入口——经类型键渲染器注册表
 * （ARTIFACT_RENDERERS / resolveArtifactRenderer，pi-web-ui 模式借用，零 import）
 * 解析产物正文；实现见 cited-answer-artifact.tsx。
 */
export { ArtifactPreview } from "./cited-answer-artifact";
export { WorldModelEvidencePanel } from "./world-model-evidence-panel";

export type AnalysisChapterRef = {
  id: number;
  chapter_number: number;
  title?: string;
};

type Props = {
  novelId: string;
  /** 章节 id/章号映射（页面已为结构树加载章节，复用同一数据）。 */
  chapters: AnalysisChapterRef[];
  /** 该小说现有的「显示全书」开关状态（timeline_full_book 偏好）。 */
  fullBook: boolean;
  /** 服务端阅读进度 chapter_id（DB id），无进度为 null。 */
  progressChapterId: number | null;
  /** 当前结构选中范围（锚点=结构）。 */
  selection: StructureNodeSelection | null;
  className?: string;
};

export function AnalysisChatPanel({
  novelId,
  chapters,
  fullBook,
  progressChapterId,
  selection,
  className,
}: Props) {
  const router = useRouter();
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<MessageView[]>([]);
  const [draft, setDraft] = useState("");
  const [loadingList, setLoadingList] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [activeJob, setActiveJob] = useState<GenerationJobView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const listRequestRef = useRef(0);
  const msgRequestRef = useRef(0);
  const pollAbortRef = useRef<AbortController | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);

  // 换书时同步重置（render 期重置，不 unmount，保住视图切换的其余状态）
  const [loadedNovel, setLoadedNovel] = useState<string | null>(null);
  if (loadedNovel !== novelId) {
    setLoadedNovel(novelId);
    setConversations([]);
    setActiveId(null);
    setMessages([]);
    setDraft("");
    setActiveJob(null);
    setError(null);
  }

  // ---------- 剧透边界 / 结构锚点派生 ----------

  /** 阅读进度章号；无进度回落到第一章（与后端 resolve_chapter_cutoff 同语义）。 */
  const cutoffChapterNumber = useMemo(() => {
    const byProgress =
      progressChapterId != null
        ? chapters.find((c) => c.id === progressChapterId)?.chapter_number ??
          null
        : null;
    if (byProgress != null) return byProgress;
    if (!chapters.length) return null;
    return chapters.reduce(
      (min, c) => Math.min(min, c.chapter_number),
      Number.POSITIVE_INFINITY
    );
  }, [chapters, progressChapterId]);

  const lastChapterNumber = useMemo(() => {
    if (!chapters.length) return null;
    return chapters.reduce((max, c) => Math.max(max, c.chapter_number), 0);
  }, [chapters]);

  const firstChapterNumber = useMemo(() => {
    if (!chapters.length) return null;
    return chapters.reduce(
      (min, c) => Math.min(min, c.chapter_number),
      Number.POSITIVE_INFINITY
    );
  }, [chapters]);

  /**
   * 区间锚点（25.1-01 契约，章号语义）：
   * 有结构选中 = 选中区间；无选中 = 第一章..（全书 ? 末章 : 进度章）。
   * 发送原始请求区间，chapter_end 由服务端按剧透边界收窄并回显。
   */
  const requestedRange = useMemo(() => {
    if (selection) {
      return { start: selection.chapterStart, end: selection.chapterEnd };
    }
    if (firstChapterNumber == null) return null;
    const end = fullBook ? lastChapterNumber : cutoffChapterNumber;
    if (end == null) return null;
    return { start: firstChapterNumber, end };
  }, [
    selection,
    fullBook,
    firstChapterNumber,
    lastChapterNumber,
    cutoffChapterNumber,
  ]);

  const rangeReady = chapters.length > 0 && requestedRange != null;

  /** 起始章已超出阅读进度：服务端必 422 chapter_beyond_cutoff，前端预判禁发。 */
  const startBeyondCutoff = Boolean(
    requestedRange &&
      !fullBook &&
      cutoffChapterNumber != null &&
      requestedRange.start > cutoffChapterNumber
  );

  /** 末章超出边界：可发送，服务端会收窄（anchor 回显实际区间）。 */
  const willNarrow = Boolean(
    requestedRange &&
      !fullBook &&
      !startBeyondCutoff &&
      cutoffChapterNumber != null &&
      requestedRange.end > cutoffChapterNumber
  );

  const boundaryLabel = fullBook
    ? "全书模式"
    : `基于你已读至第 ${cutoffChapterNumber ?? 1} 章`;

  // ---------- 会话 / 消息加载（与 reader chat 同一底座） ----------

  const refreshConversations = useCallback(async () => {
    const req = ++listRequestRef.current;
    setLoadingList(true);
    try {
      const res = await readerChatApi.listConversations(novelId, { limit: 50 });
      if (req !== listRequestRef.current) return;
      setConversations(res.data.items);
      setError(null);
      setActiveId((prev) => {
        if (prev && res.data.items.some((c) => c.id === prev)) return prev;
        const firstActive = res.data.items.find((c) => c.status === "active");
        return firstActive?.id ?? res.data.items[0]?.id ?? null;
      });
    } catch {
      if (req === listRequestRef.current) setError("加载会话列表失败");
    } finally {
      if (req === listRequestRef.current) setLoadingList(false);
    }
  }, [novelId]);

  const refreshMessages = useCallback(
    async (conversationId: number) => {
      const req = ++msgRequestRef.current;
      setLoadingMessages(true);
      try {
        const res = await readerChatApi.listMessages(novelId, conversationId, {
          limit: 200,
        });
        if (req !== msgRequestRef.current) return;
        setMessages(res.data.items);
        setError(null);
        const lastUser = [...res.data.items]
          .reverse()
          .find((m) => m.role === "user" && m.generation_job);
        const job = lastUser?.generation_job ?? null;
        if (job && !isTerminalJobStatus(job.status)) {
          setActiveJob(job);
        } else if (job && isTerminalJobStatus(job.status)) {
          setActiveJob(job.status === "completed" ? null : job);
        } else {
          setActiveJob(null);
        }
      } catch {
        if (req === msgRequestRef.current) setError("加载消息失败");
      } finally {
        if (req === msgRequestRef.current) setLoadingMessages(false);
      }
    },
    [novelId]
  );

  useEffect(() => {
    if (!novelId) return;
    let cancelled = false;
    void (async () => {
      await refreshConversations();
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [novelId, refreshConversations]);

  useEffect(() => {
    if (activeId == null) return;
    let cancelled = false;
    void (async () => {
      await refreshMessages(activeId);
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId, refreshMessages]);

  // 新消息就位后滚到底（scrollTop 赋值，无动效）
  useEffect(() => {
    const el = messagesRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  // 轮询未完成的生成任务（复用 reader chat 轮询器）
  useEffect(() => {
    if (activeId == null || !activeJob) return;
    if (isTerminalJobStatus(activeJob.status)) return;

    pollAbortRef.current?.abort();
    const ac = new AbortController();
    pollAbortRef.current = ac;
    const jobId = activeJob.id;
    const convId = activeId;

    void (async () => {
      try {
        const terminal = await pollReaderChatJob(novelId, convId, jobId, {
          signal: ac.signal,
          intervalMs: 700,
          onUpdate: (j) => setActiveJob(j),
        });
        setActiveJob(terminal.status === "completed" ? null : terminal);
        await refreshMessages(convId);
        await refreshConversations();
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        const status = (err as { response?: { status?: number } })?.response
          ?.status;
        if (status === 404) {
          setActiveJob(null);
          setError("任务已失效或不属于当前会话，请重新发送");
          return;
        }
        setError("任务状态同步失败，请稍后重试");
      }
    })();

    return () => ac.abort();
    // 轮询 key 只看 job id/status，避免整对象引用反复重启轮询
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll key is job id/status
  }, [
    activeId,
    activeJob?.id,
    activeJob?.status,
    novelId,
    refreshMessages,
    refreshConversations,
  ]);

  const activeConversation = useMemo(
    () => conversations.find((c) => c.id === activeId) ?? null,
    [conversations, activeId]
  );

  // ---------- 交互 ----------

  const handleCreate = async () => {
    setError(null);
    try {
      const res = await readerChatApi.createConversation(
        novelId,
        `会话 ${conversations.length + 1}`
      );
      await refreshConversations();
      setActiveId(res.data.id);
    } catch {
      setError("创建会话失败");
    }
  };

  const handleSend = async () => {
    if (!draft.trim()) {
      setError("请输入问题");
      return;
    }
    if (!rangeReady || !requestedRange) {
      setError("章节数据尚未加载，暂时无法发送");
      return;
    }
    if (startBeyondCutoff) {
      setError("起始章超出阅读进度，请调整结构选中范围或开启全书模式");
      return;
    }
    let conversationId = activeId;
    setSending(true);
    setError(null);
    try {
      if (conversationId == null) {
        const created = await readerChatApi.createConversation(
          novelId,
          "新会话"
        );
        conversationId = created.data.id;
        setActiveId(conversationId);
      }
      if (activeConversation?.status === "archived") {
        setError("已归档会话不可发送");
        setSending(false);
        return;
      }
      const body = draft.trim();
      setDraft("");
      // 25.1-01 区间锚点：与 chapter_id/selection 互斥，服务端收窄 chapter_end。
      const accepted = await readerChatApi.createMessage(
        novelId,
        conversationId,
        {
          client_message_id: newClientMessageId(),
          body,
          chapter_range: {
            chapter_start: requestedRange.start,
            chapter_end: requestedRange.end,
          },
        }
      );
      setMessages((prev) => {
        if (prev.some((m) => m.id === accepted.data.message.id)) return prev;
        return [...prev, accepted.data.message];
      });
      setActiveJob(accepted.data.job);
      await refreshConversations();
    } catch (err: unknown) {
      const resp = (
        err as {
          response?: {
            status?: number;
            data?: { detail?: string | { code?: string; message?: string } };
          };
        }
      )?.response;
      const status = resp?.status;
      const detail = resp?.data?.detail;
      if (status === 409) {
        setError(typeof detail === "string" ? detail : "会话冲突");
      } else if (status === 422) {
        // 25.1-01 起 detail 为 { code, message } 结构化对象
        if (typeof detail === "object" && detail?.code === "chapter_beyond_cutoff") {
          setError("起始章超出阅读进度，请调整结构选中范围或开启全书模式");
        } else if (typeof detail === "object" && detail?.message) {
          setError(detail.message);
        } else {
          setError(typeof detail === "string" ? detail : "上下文锚点无效");
        }
      } else {
        setError("发送失败");
      }
    } finally {
      setSending(false);
    }
  };

  const handleCancel = async () => {
    if (activeId == null || !activeJob) return;
    try {
      const res = await readerChatApi.cancelJob(novelId, activeId, activeJob.id);
      setActiveJob(res.data);
    } catch {
      setError("取消失败");
    }
  };

  const handleRetry = async () => {
    if (activeId == null || !activeJob) return;
    try {
      const res = await readerChatApi.retryJob(novelId, activeId, activeJob.id);
      setActiveJob(res.data);
    } catch {
      setError("重试失败");
    }
  };

  /** citation → 阅读页对应章节（与时间线跳转同款：不覆盖真实阅读进度）。 */
  const handleCitationNavigate = (target: CitationNavigateTarget) => {
    const params = new URLSearchParams();
    params.set("chapter", String(target.chapter_id));
    params.set("start", String(target.source_start));
    params.set("end", String(target.source_end));
    params.set("from", "timeline");
    router.push(`/novels/${novelId}?${params.toString()}`);
  };

  const inputDisabled =
    sending ||
    activeConversation?.status === "archived" ||
    (!!activeJob && !isTerminalJobStatus(activeJob.status));

  return (
    <div
      data-testid="analysis-chat-panel"
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-2xl border border-border/60 bg-card text-sm motion-transition-content",
        className
      )}
    >
      {/* 会话列表 */}
      <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border/50 px-2 py-1.5">
        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-label="新建会话"
          onClick={() => void handleCreate()}
        >
          <MessageSquarePlus className="size-3.5" />
        </Button>
        {loadingList ? (
          <LoaderCircle
            className="size-3.5 shrink-0 animate-spin text-muted-foreground"
            aria-hidden
          />
        ) : null}
        {conversations.map((c) => (
          <button
            key={c.id}
            type="button"
            data-testid={`analysis-chat-conv-${c.id}`}
            className={cn(
              "max-w-[7rem] shrink-0 truncate rounded-full px-2.5 py-1 text-xs motion-transition-feedback",
              c.id === activeId
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-muted/80",
              c.status === "archived" && "opacity-70"
            )}
            onClick={() => setActiveId(c.id)}
          >
            {c.title}
            {c.status === "archived" ? "·档" : ""}
          </button>
        ))}
      </div>

      {/* 消息流 */}
      <div
        ref={messagesRef}
        data-testid="analysis-chat-messages"
        className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3"
      >
        {loadingMessages ? (
          <p className="text-center text-xs text-muted-foreground">加载消息…</p>
        ) : null}
        {!loadingMessages && messages.length === 0 ? (
          <div
            data-testid="analysis-chat-empty"
            className="grid min-h-24 place-items-center text-center text-xs text-muted-foreground"
          >
            <p>
              还没有消息。基于当前结构范围直接提问；
              回答会附带可跳转到原文的引用。
            </p>
          </div>
        ) : null}
        {messages.map((m) => (
          <div key={m.id} className="space-y-0.5">
            {m.anchor?.kind === "chapter_range" ? (
              <p
                data-testid={`analysis-chat-msg-anchor-${m.id}`}
                className="px-1 text-right text-[11px] text-muted-foreground"
              >
                范围：
                {formatChapterRange(
                  m.anchor.chapter_start,
                  m.anchor.chapter_end
                )}
              </p>
            ) : null}
            <MessageBubble
              message={m}
              onCitationNavigate={handleCitationNavigate}
            />
            {m.queryplan ? (
              <p
                data-testid={`analysis-chat-queryplan-${m.id}`}
                className="px-1 text-[10px] leading-snug text-muted-foreground/70"
              >
                QueryPlan · {m.queryplan.intent === "reader" ? "读者" : "分析"}
                {m.queryplan.anchor_kind === "selection"
                  ? " · 选区锚点"
                  : m.queryplan.anchor_kind === "chapter_range"
                    ? " · 结构区间锚点"
                    : " · 无锚点"}
                {m.queryplan.full_book_authorized
                  ? " · 全书模式"
                  : ` · 已读至第 ${m.queryplan.through_chapter} 章`}
                {m.queryplan.abstained
                  ? " · 已弃权（证据不足）"
                  : ` · 引用 ${m.queryplan.allowed_evidence_ids.length}`}
                {m.queryplan.availability.some(
                  (a) => a.status === "unavailable" || a.status === "partial"
                )
                  ? " · 部分维度不可用"
                  : ""}
              </p>
            ) : null}
            {m.queryplan?.world_projection ? (
              <div className="px-1 pt-1">
                <WorldModelEvidencePanel
                  novelId={novelId}
                  worldProjection={m.queryplan.world_projection}
                  onCitationNavigate={handleCitationNavigate}
                />
              </div>
            ) : null}
            {m.backfill_runs && m.backfill_runs.length > 0 ? (
              <div
                data-testid={`analysis-chat-backfill-${m.id}`}
                className="px-1 pt-1"
              >
                {m.backfill_runs.map((br) => {
                  const active =
                    br.status === "queued" || br.status === "running";
                  const skillLabel =
                    br.skill_name === "detect-key-scenes"
                      ? "关键场景"
                      : br.skill_name === "propose-world-model-candidates"
                        ? "世界模型"
                        : br.skill_name === "build-visual-bible"
                          ? "视觉圣经"
                          : br.skill_name === "build-story-arc"
                            ? "故事弧"
                            : br.skill_name === "analyze-chapter"
                              ? "章节分析"
                              : br.skill_name;
                  return (
                    <span
                      key={br.run_id}
                      className="mr-2 inline-flex items-center gap-1 rounded-md border border-muted bg-muted/40 px-2 py-0.5 text-[10px] text-muted-foreground"
                    >
                      {active ? (
                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
                      ) : null}
                      {active ? "后台分析中" : "后台分析完成"} · {skillLabel}
                      {br.backfill_dimension
                        ? `（${br.backfill_dimension}）`
                        : ""}
                    </span>
                  );
                })}
              </div>
            ) : null}
          </div>
        ))}
        {activeJob && jobStatusLabel(activeJob) ? (
          <div
            data-testid="analysis-chat-job-status"
            data-status={activeJob.status}
            aria-live="polite"
            className="rounded-lg border border-border/60 bg-muted/40 px-3 py-2 text-xs"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5">
                {!isTerminalJobStatus(activeJob.status) ? (
                  <LoaderCircle className="size-3.5 animate-spin" />
                ) : null}
                {jobStatusLabel(activeJob)}
              </span>
              <span className="flex gap-1">
                {!isTerminalJobStatus(activeJob.status) ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    aria-label="取消生成"
                    onClick={() => void handleCancel()}
                  >
                    <XCircle className="size-3.5" />
                    取消
                  </Button>
                ) : null}
                {activeJob.status === "paused_budget" ||
                activeJob.status === "paused_dependency" ||
                activeJob.status === "failed" ||
                activeJob.status === "failed_validation" ||
                activeJob.status === "cancelled" ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    aria-label="重试生成"
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
      </div>

      {/* 上下文提示：结构范围 + 剧透边界 + 单章锚点降级说明 */}
      <div
        data-testid="analysis-chat-context"
        className="shrink-0 space-y-0.5 border-t border-border/50 bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground"
      >
        <p>
          范围：
          {selection ? (
            <>
              <span className="font-medium text-foreground">
                {formatChapterRange(selection.chapterStart, selection.chapterEnd)}
              </span>
              <span className="ml-1">{selection.label}</span>
            </>
          ) : (
            <span>未选择结构节点</span>
          )}
          <span className="mx-1.5" aria-hidden>
            ·
          </span>
          <span data-testid="analysis-chat-boundary">{boundaryLabel}</span>
        </p>
        {rangeReady && requestedRange ? (
          <p data-testid="analysis-chat-anchor-note">
            上下文范围：
            {formatChapterRange(requestedRange.start, requestedRange.end)}
            {startBeyondCutoff
              ? "（起始章超出阅读进度，无法发送）"
              : willNarrow
                ? "（末章超出阅读进度，将按已读边界收窄）"
                : ""}
          </p>
        ) : (
          <p data-testid="analysis-chat-anchor-note">
            章节数据尚未加载，暂时无法发送
          </p>
        )}
      </div>

      {error ? (
        <div
          data-testid="analysis-chat-error"
          className="shrink-0 px-3 py-1 text-xs text-destructive"
          role="alert"
        >
          {error}
        </div>
      ) : null}

      {/* 输入 */}
      <div className="flex shrink-0 items-end gap-2 border-t border-border/70 p-2">
        <textarea
          data-testid="analysis-chat-input"
          className="min-h-[2.5rem] max-h-24 min-w-0 flex-1 resize-y rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          placeholder={
            activeConversation?.status === "archived"
              ? "已归档，无法发送"
              : selection
                ? `针对${formatChapterRange(selection.chapterStart, selection.chapterEnd)}提问…`
                : "针对本书提问…"
          }
          value={draft}
          disabled={inputDisabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void handleSend();
            }
          }}
          aria-label="分析对话输入"
        />
        <Button
          type="button"
          size="sm"
          data-testid="analysis-chat-send"
          disabled={
            sending ||
            !draft.trim() ||
            !rangeReady ||
            startBeyondCutoff ||
            activeConversation?.status === "archived"
          }
          onClick={() => void handleSend()}
        >
          {sending ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
        </Button>
      </div>
    </div>
  );
}
