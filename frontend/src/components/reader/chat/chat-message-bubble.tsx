"use client";

import { cn } from "@/lib/utils";
import type { CitationView, MessageView } from "@/lib/api";

export type CitationNavigateTarget = {
  chapter_id: number;
  source_start: number;
  source_end: number;
  evidence_key: string;
};

/** 共享引用跳转目标（reader-chat-panel 与 analysis 域共用）。 */
export function MessageBubble({
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
          {message.citations
            .filter(
              // Defensive spoiler/quality gate: only render citations with a
              // valid non-empty code-point range (D-06 spoiler-safe rendering).
              (c) =>
                c.chapter_id > 0 &&
                Number.isInteger(c.source_start) &&
                Number.isInteger(c.source_end) &&
                c.source_end > c.source_start
            )
            .map((c) => (
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

export function CitationChip({
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
      aria-label={`跳转到引用原文：第 ${citation.chapter_id} 章片段 @${citation.source_start}`}
      className="rounded-full border border-primary/30 bg-primary/5 px-2 py-0.5 text-[11px] text-primary transition-colors hover:bg-primary/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
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
