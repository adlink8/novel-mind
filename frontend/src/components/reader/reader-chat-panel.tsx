"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  ChevronDown,
  ChevronUp,
  LoaderCircle,
  MessageSquarePlus,
  Pencil,
  Send,
  Trash2,
  X,
  XCircle,
  RotateCcw,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  isTerminalJobStatus,
  pollReaderChatJob,
  readerChatApi,
  type CitationView,
  type ConversationListItem,
  type GenerationJobView,
  type MessageView,
  type SelectionCoordinate,
} from "@/lib/api";
import {
  loadReaderChatPresentation,
  saveReaderChatPresentation,
} from "@/lib/reader-selection";
import { useDismissableLayer } from "@/lib/use-dismissable-layer";
import { cn } from "@/lib/utils";

export type CitationNavigateTarget = {
  chapter_id: number;
  source_start: number;
  source_end: number;
  evidence_key: string;
};

type Props = {
  novelId: string;
  currentChapterId: number;
  /** Desktop side panel reserves space; mobile is a bounded bottom sheet. */
  layout: "desktop" | "mobile";
  open: boolean;
  collapsed: boolean;
  onOpenChange: (open: boolean) => void;
  onCollapsedChange: (collapsed: boolean) => void;
  /** Immutable selection payload captured before DOM selection collapses. */
  pendingSelection: SelectionCoordinate | null;
  onClearSelection: () => void;
  onCitationNavigate: (target: CitationNavigateTarget) => void;
  className?: string;
};

function newClientMessageId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `cm-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function jobStatusLabel(job: GenerationJobView | null | undefined): string | null {
  if (!job) return null;
  const reason = (job.status_reason || job.error_code || "").trim();
  switch (job.status) {
    case "queued":
      return "排队中…";
    case "running":
      return "生成中…";
    case "paused_budget":
      return reason ? `预算已暂停：${reason}` : "预算已暂停";
    case "paused_dependency":
      return reason ? `模型/依赖不可用：${reason}` : "模型或依赖不可用（可点重试）";
    case "cancelled":
      return "已取消";
    case "failed":
    case "failed_validation":
      return job.error_code ? `失败：${job.error_code}` : "生成失败";
    case "completed":
      return null;
    default:
      return job.status;
  }
}

export function ReaderChatPanel({
  novelId,
  currentChapterId,
  layout,
  open,
  collapsed,
  onOpenChange,
  onCollapsedChange,
  pendingSelection,
  onClearSelection,
  onCitationNavigate,
  className,
}: Props) {
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<MessageView[]>([]);
  const [draft, setDraft] = useState("");
  const [loadingList, setLoadingList] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [activeJob, setActiveJob] = useState<GenerationJobView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState<string | null>(null);
  const pollAbortRef = useRef<AbortController | null>(null);
  const listRequestRef = useRef(0);
  const msgRequestRef = useRef(0);
  const panelRef = useRef<HTMLDivElement>(null);

  // Expanded panel only — collapsed chip is not a dismissable surface.
  const dismissableOpen = open && !(layout === "mobile" && collapsed);
  const { present, closing } = useDismissableLayer({
    open: dismissableOpen,
    onDismiss: () => onOpenChange(false),
    layerRef: panelRef,
    ignoreSelectors: ["[data-reader-chat-toggle]"],
    // Desktop reserved column: outside click still closes; mobile sheet same.
    closeOnOutside: true,
  });

  // Restore presentation-only active conversation id (lazy init alternative for novel change)
  const [hydratedNovel, setHydratedNovel] = useState<string | null>(null);
  if (hydratedNovel !== novelId) {
    setHydratedNovel(novelId);
    const saved = loadReaderChatPresentation(novelId);
    if (saved.activeConversationId != null) {
      setActiveId(saved.activeConversationId);
    }
  }

  useEffect(() => {
    const prev = loadReaderChatPresentation(novelId);
    saveReaderChatPresentation(novelId, {
      ...prev,
      open,
      collapsed,
      activeConversationId: activeId,
    });
  }, [novelId, open, collapsed, activeId]);

  const refreshConversations = useCallback(async () => {
    const req = ++listRequestRef.current;
    setLoadingList(true);
    try {
      const res = await readerChatApi.listConversations(novelId, { limit: 50 });
      if (req !== listRequestRef.current) return;
      setConversations(res.data.items);
      setActiveId((prev) => {
        if (prev && res.data.items.some((c) => c.id === prev)) return prev;
        const firstActive = res.data.items.find((c) => c.status === "active");
        return firstActive?.id ?? res.data.items[0]?.id ?? null;
      });
    } catch {
      if (req === listRequestRef.current) {
        setError("加载会话列表失败");
      }
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
        // Surface non-terminal job from latest user message
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
    if (!open) return;
    let cancelled = false;
    void (async () => {
      await refreshConversations();
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [open, refreshConversations]);

  useEffect(() => {
    if (!open || activeId == null) {
      return;
    }
    let cancelled = false;
    void (async () => {
      await refreshMessages(activeId);
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [open, activeId, refreshMessages]);

  // When panel closes or conversation cleared, drop local message buffer.
  useEffect(() => {
    if (!open || activeId == null) {
      queueMicrotask(() => setMessages([]));
    }
  }, [open, activeId]);

  // Poll active non-terminal job
  useEffect(() => {
    if (!open || activeId == null || !activeJob) return;
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
        setActiveJob(
          isTerminalJobStatus(terminal.status) && terminal.status === "completed"
            ? null
            : terminal
        );
        await refreshMessages(convId);
        await refreshConversations();
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 404) {
          setActiveJob(null);
          setError("任务已失效或不属于当前会话，请重新发送");
          return;
        }
        setError("任务状态同步失败，请稍后重试");
      }
    })();

    return () => ac.abort();
    // activeJob fields intentionally narrow — full object would restart poll each tick
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll key is job id/status
  }, [
    open,
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

  const handleRename = async () => {
    if (activeId == null || !renameDraft?.trim()) {
      setRenameDraft(null);
      return;
    }
    try {
      await readerChatApi.patchConversation(novelId, activeId, {
        title: renameDraft.trim(),
      });
      setRenameDraft(null);
      await refreshConversations();
    } catch {
      setError("重命名失败");
    }
  };

  const handleArchiveToggle = async () => {
    if (activeId == null || !activeConversation) return;
    const next =
      activeConversation.status === "archived" ? "active" : "archived";
    try {
      await readerChatApi.patchConversation(novelId, activeId, { status: next });
      await refreshConversations();
    } catch {
      setError(next === "archived" ? "归档失败" : "恢复失败");
    }
  };

  const handleDelete = async () => {
    if (activeId == null) return;
    try {
      await readerChatApi.deleteConversation(novelId, activeId);
      setActiveId(null);
      setMessages([]);
      await refreshConversations();
    } catch {
      setError("删除失败");
    }
  };

  const handleSend = async () => {
    if (!currentChapterId) {
      setError("当前章节尚未加载");
      return;
    }
    if (!draft.trim()) {
      setError("请输入问题");
      return;
    }
    let conversationId = activeId;
    setSending(true);
    setError(null);
    try {
      if (conversationId == null) {
        const created = await readerChatApi.createConversation(novelId, "新会话");
        conversationId = created.data.id;
        setActiveId(conversationId);
      }
      if (activeConversation?.status === "archived") {
        setError("已归档会话不可发送");
        setSending(false);
        return;
      }
      // Capture an optional selection immutably. Without one, the server derives
      // context from the owned current chapter and the frozen spoiler cutoff.
      const selection: SelectionCoordinate | undefined = pendingSelection
        ? { ...pendingSelection }
        : undefined;
      const body = draft.trim();
      setDraft("");
      const accepted = await readerChatApi.createMessage(novelId, conversationId, {
        client_message_id: newClientMessageId(),
        body,
        chapter_id: currentChapterId,
        ...(selection ? { selection } : {}),
      });
      // Do not optimistically invent assistant text — only show user message + job.
      setMessages((prev) => {
        if (prev.some((m) => m.id === accepted.data.message.id)) return prev;
        return [...prev, accepted.data.message];
      });
      setActiveJob(accepted.data.job);
      onClearSelection();
      await refreshConversations();
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number; data?: { detail?: string } } })
        ?.response?.status;
      const detail = (err as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail;
      if (status === 409) {
        setError(typeof detail === "string" ? detail : "会话冲突");
      } else if (status === 422) {
        setError(typeof detail === "string" ? detail : "选区无效或已过期");
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

  if (!open && !present) return null;

  // Mobile collapsed chip
  if (layout === "mobile" && collapsed) {
    return (
      <button
        type="button"
        data-testid="reader-chat-chip"
        className="fixed bottom-20 right-3 z-40 flex items-center gap-2 rounded-full border border-border bg-card px-3 py-2 text-sm shadow-lg transition-[opacity,transform] motion-duration-fast motion-ease-enter"
        onClick={() => onCollapsedChange(false)}
      >
        <MessageSquarePlus className="size-4" />
        对话
        {activeJob && !isTerminalJobStatus(activeJob.status) ? (
          <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
        ) : null}
        {activeJob && !isTerminalJobStatus(activeJob.status) ? (
          <span className="sr-only">生成中</span>
        ) : null}
      </button>
    );
  }

  // Desktop collapsed: slim rail only (no full header squeezed into w-12).
  if (layout === "desktop" && collapsed && open) {
    return (
      <div
        data-testid="reader-chat-rail"
        className={cn(
          "flex h-full w-full flex-col items-center gap-2 border-l border-border bg-card py-3",
          className
        )}
      >
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-auto w-9 flex-col gap-1 px-1 py-2"
          aria-label="展开对话"
          data-testid="reader-chat-expand"
          onClick={() => onCollapsedChange(false)}
        >
          <MessageSquarePlus className="size-4" />
          <span
            className="text-[10px] leading-tight text-muted-foreground"
            style={{ writingMode: "vertical-rl" }}
          >
            展开
          </span>
          {activeJob && !isTerminalJobStatus(activeJob.status) ? (
            <LoaderCircle className="size-3 animate-spin" aria-hidden />
          ) : null}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="mt-auto w-9"
          aria-label="关闭对话"
          onClick={() => onOpenChange(false)}
        >
          <X className="size-4" />
        </Button>
      </div>
    );
  }

  if (!present) return null;

  const panelBody = (
    <div
      ref={panelRef}
      data-testid="reader-chat-panel"
      data-layout={layout}
      aria-hidden={closing || undefined}
      className={cn(
        "flex h-full min-h-0 w-full flex-col bg-card text-sm",
        // Desktop column: no off-axis translate (avoids “stuck on right edge”).
        layout === "desktop" &&
          "border-l border-border transition-opacity motion-duration-spatial",
        layout === "mobile" &&
          "max-h-[45vh] rounded-t-2xl border border-border shadow-2xl transition-[opacity,transform] motion-duration-spatial motion-ease-enter",
        open && !closing
          ? layout === "mobile"
            ? "translate-y-0 opacity-100"
            : "opacity-100"
          : layout === "mobile"
            ? "pointer-events-none translate-y-2 opacity-0 motion-ease-exit"
            : "pointer-events-none opacity-0 motion-ease-exit",
        className
      )}
    >
      <header className="flex shrink-0 items-center justify-between gap-2 border-b border-border/70 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-medium">选区对话</span>
          {loadingList ? (
            <LoaderCircle className="size-3.5 animate-spin text-muted-foreground" />
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          {layout === "mobile" ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label="收起对话"
              onClick={() => onCollapsedChange(true)}
            >
              <ChevronDown className="size-4" />
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label="折叠对话"
              onClick={() => onCollapsedChange(true)}
            >
              <ChevronUp className="size-4" />
            </Button>
          )}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-label="关闭对话"
            onClick={() => onOpenChange(false)}
          >
            <X className="size-4" />
          </Button>
        </div>
      </header>

      <>
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
            {conversations.map((c) => (
              <button
                key={c.id}
                type="button"
                data-testid={`reader-chat-conv-${c.id}`}
                className={cn(
                  "max-w-[7rem] truncate rounded-full px-2.5 py-1 text-xs",
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

          {activeConversation ? (
            <div className="flex shrink-0 items-center gap-1 border-b border-border/40 px-2 py-1">
              {renameDraft != null ? (
                <>
                  <input
                    className="min-w-0 flex-1 rounded border border-border px-2 py-0.5 text-xs"
                    value={renameDraft}
                    onChange={(e) => setRenameDraft(e.target.value)}
                    aria-label="会话标题"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void handleRename();
                      if (e.key === "Escape") setRenameDraft(null);
                    }}
                  />
                  <Button type="button" size="sm" variant="ghost" onClick={() => void handleRename()}>
                    保存
                  </Button>
                </>
              ) : (
                <>
                  <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                    {activeConversation.title}
                    {activeConversation.status === "archived" ? "（已归档）" : ""}
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label="重命名会话"
                    onClick={() => setRenameDraft(activeConversation.title)}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={
                      activeConversation.status === "archived"
                        ? "恢复会话"
                        : "归档会话"
                    }
                    onClick={() => void handleArchiveToggle()}
                  >
                    {activeConversation.status === "archived" ? (
                      <ArchiveRestore className="size-3.5" />
                    ) : (
                      <Archive className="size-3.5" />
                    )}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label="删除会话"
                    onClick={() => void handleDelete()}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </>
              )}
            </div>
          ) : null}

          <div
            data-testid="reader-chat-messages"
            className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3"
          >
            {loadingMessages ? (
              <p className="text-center text-xs text-muted-foreground">加载消息…</p>
            ) : null}
            {!loadingMessages && messages.length === 0 ? (
              <p
                data-testid="reader-chat-empty"
                className="text-center text-xs text-muted-foreground"
              >
                可直接针对当前章节提问；选中正文后会优先绑定该片段。
              </p>
            ) : null}
            {messages.map((m) => (
              <MessageBubble
                key={m.id}
                message={m}
                onCitationNavigate={onCitationNavigate}
              />
            ))}
            {activeJob && jobStatusLabel(activeJob) ? (
              <div
                data-testid="reader-chat-job-status"
                data-status={activeJob.status}
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

          {pendingSelection ? (
            <div
              data-testid="reader-chat-selection-preview"
              className="shrink-0 border-t border-border/50 bg-amber-50/80 px-3 py-1.5 text-xs text-amber-950"
            >
              选区 [{pendingSelection.source_start}, {pendingSelection.source_end})：
              <span className="ml-1 font-medium">
                {pendingSelection.selection_text.slice(0, 48)}
                {pendingSelection.selection_text.length > 48 ? "…" : ""}
              </span>
            </div>
          ) : (
            <div className="shrink-0 border-t border-border/50 px-3 py-1.5 text-xs text-muted-foreground">
              当前章节提问模式 · 选中正文可绑定更精确的证据
            </div>
          )}

          {error ? (
            <div
              data-testid="reader-chat-error"
              className="shrink-0 px-3 py-1 text-xs text-destructive"
              role="alert"
            >
              {error}
            </div>
          ) : null}

          <div className="flex shrink-0 items-end gap-2 border-t border-border/70 p-2">
            <textarea
              data-testid="reader-chat-input"
              className="min-h-[2.5rem] max-h-24 flex-1 resize-y rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              placeholder={
                activeConversation?.status === "archived"
                  ? "已归档，无法发送"
                  : pendingSelection
                    ? "针对选区提问…"
                    : "针对当前章节提问…"
              }
              value={draft}
              disabled={
                sending ||
                activeConversation?.status === "archived" ||
                (!!activeJob && !isTerminalJobStatus(activeJob.status))
              }
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              aria-label="对话输入"
            />
            <Button
              type="button"
              size="sm"
              data-testid="reader-chat-send"
              disabled={
                sending ||
                !draft.trim() ||
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
        </>
    </div>
  );

  if (layout === "mobile") {
    return (
      <div className="fixed inset-x-0 bottom-14 z-40 px-0 sm:px-2">
        {panelBody}
      </div>
    );
  }

  return panelBody;
}

function MessageBubble({
  message,
  onCitationNavigate,
}: {
  message: MessageView;
  onCitationNavigate: (t: CitationNavigateTarget) => void;
}) {
  const isUser = message.role === "user";
  return (
    <div
      data-testid={`reader-chat-msg-${message.id}`}
      data-role={message.role}
      className={cn(
        "rounded-xl px-3 py-2",
        isUser ? "ml-6 bg-primary/10" : "mr-4 border border-border/60 bg-background"
      )}
    >
      <p className="whitespace-pre-wrap text-[13px] leading-relaxed">{message.body}</p>
      {message.selection ? (
        <p className="mt-1 text-[10px] text-muted-foreground">
          选区 ch{message.selection.chapter_id} [{message.selection.source_start},
          {message.selection.source_end})
        </p>
      ) : null}
      {!isUser && message.citations.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {message.citations.map((c) => (
            <CitationChip
              key={`${c.block_id}-${c.context_evidence_ref_id}`}
              citation={c}
              onNavigate={onCitationNavigate}
            />
          ))}
        </div>
      ) : null}
      {message.body.includes("[suggestion:") ? (
        <p
          data-testid="reader-chat-suggestion-note"
          className="mt-1 text-[10px] text-muted-foreground"
        >
          建议仅供展示，需显式确认后才能写入（本阶段无应用入口）
        </p>
      ) : null}
    </div>
  );
}

function CitationChip({
  citation,
  onNavigate,
}: {
  citation: CitationView;
  onNavigate: (t: CitationNavigateTarget) => void;
}) {
  return (
    <button
      type="button"
      data-testid="reader-chat-citation"
      data-source-start={citation.source_start}
      data-chapter-id={citation.chapter_id}
      className="rounded-full border border-primary/30 bg-primary/5 px-2 py-0.5 text-[11px] text-primary hover:bg-primary/10"
      onClick={() =>
        onNavigate({
          chapter_id: citation.chapter_id,
          source_start: citation.source_start,
          source_end: citation.source_end,
          evidence_key: citation.evidence_key,
        })
      }
    >
      引用 · ch{citation.chapter_id} @{citation.source_start}
    </button>
  );
}
