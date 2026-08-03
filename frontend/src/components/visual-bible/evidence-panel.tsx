"use client";

/**
 * Phase 30 Visual Bible — Evidence panel (REQ-VIS-01, D-30-02/D-30-04).
 *
 * Renders the evidence refs attached to a typed visual claim exactly as the
 * server envelope provides them:
 *
 * - chapter/range, source snapshot, content hash and spoiler cutoff are shown
 *   (the client never recomputes or re-validates evidence);
 * - every ref carries a leaf jump back to the reader chapter range;
 * - a canon_fact claim that somehow has no evidence refs is surfaced as an
 *   unresolved marker and is never presented as approved/canon (fail closed).
 */

import { useRouter } from "next/navigation";

import type {
  VisualClaimView,
  VisualEvidenceRefView,
} from "@/lib/visual-bible-api";
import { shortVisualHash } from "@/lib/visual-bible-api";
import { cn } from "@/lib/utils";

export type VisualEvidenceJumpTarget = {
  chapter_id: number;
  source_start: number;
  source_end: number;
  evidence_key: string;
};

export type EvidencePanelProps = {
  claim: VisualClaimView;
  novelId: string | number;
  onCitationNavigate?: (target: VisualEvidenceJumpTarget) => void;
  className?: string;
};

export function EvidencePanel({
  claim,
  novelId,
  onCitationNavigate,
  className,
}: EvidencePanelProps) {
  const router = useRouter();
  const refs = claim.evidence_refs ?? [];

  const handleNavigate = (ref: VisualEvidenceRefView) => {
    const target: VisualEvidenceJumpTarget = {
      chapter_id: ref.chapter_id,
      source_start: ref.source_start,
      source_end: ref.source_end,
      evidence_key: ref.evidence_key,
    };
    if (onCitationNavigate) {
      onCitationNavigate(target);
      return;
    }
    const params = new URLSearchParams();
    params.set("chapter", String(ref.chapter_id));
    params.set("start", String(ref.source_start));
    params.set("from", "visual-bible");
    router.push(`/novels/${novelId}?${params.toString()}`);
  };

  // Fail closed: a canon claim without evidence must be visible as unresolved,
  // never silently treated as approved/canon (D-30-02).
  if (claim.authority === "canon_fact" && refs.length === 0) {
    return (
      <div
        data-testid="visual-bible-claim-unresolved"
        className={cn(
          "rounded-md border border-rose-500/30 bg-rose-500/5 px-2 py-1.5 text-[11px] text-rose-800",
          className
        )}
      >
        正典事实缺少证据引用 — 未通过验证，不可审批
      </div>
    );
  }

  if (refs.length === 0) return null;

  return (
    <ul
      data-testid="visual-bible-evidence-panel"
      className={cn("space-y-1.5", className)}
    >
      {refs.map((ref) => (
        <li
          key={ref.evidence_key}
          data-testid="visual-bible-evidence-ref"
          data-evidence-key={ref.evidence_key}
          className="rounded-md border border-border/60 bg-muted/40 px-2 py-1.5"
        >
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-foreground">
            <span className="font-medium">
              第 {ref.chapter_number} 章 · 范围 {ref.source_start}–{ref.source_end}
            </span>
            <span className="text-muted-foreground">
              截止第 {ref.cutoff_chapter} 章
            </span>
            <button
              type="button"
              data-testid="visual-bible-evidence-jump"
              data-chapter-id={ref.chapter_id}
              data-source-start={ref.source_start}
              aria-label={`跳转到引用原文：第 ${ref.chapter_number} 章 @${ref.source_start}`}
              className="rounded-full border border-primary/30 bg-primary/5 px-2 py-0.5 text-[11px] text-primary transition-colors hover:bg-primary/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              onClick={() => handleNavigate(ref)}
            >
              跳转到原文
            </button>
          </div>
          {ref.excerpt ? (
            <p className="mt-1 text-[11px] italic leading-snug text-muted-foreground">
              …{ref.excerpt}…
            </p>
          ) : null}
          <div className="mt-1 flex flex-wrap gap-1.5 font-mono text-[10px] text-muted-foreground">
            <span data-testid="visual-bible-evidence-hash">
              content {shortVisualHash(ref.content_hash)}
            </span>
            <span>
              snapshot {shortVisualHash(ref.source_snapshot_hash)}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
