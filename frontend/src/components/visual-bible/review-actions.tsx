"use client";

/**
 * Phase 30 Visual Bible — explicit review actions (REQ-VIS-01, D-30-04).
 *
 * The browser only ever submits one explicit review action; the server decides
 * the legal transition and returns the derived review state. Review truth is
 * never saved client-side (30-PATTERNS.md anti-pattern: no review truth in the
 * browser).
 *
 * - `LEGAL_VISUAL_REVIEW_ACTIONS` mirrors the backend transition map as a
 *   display hint only — the server remains the final judge.
 * - The component renders the legal action buttons for the current review
 *   state, an optional audit reason, and a locked state for terminal revisions.
 * - Actions stay candidate-only: approval from the UI never promotes a
 *   generated asset or an interpretation to canon by itself.
 */

import { useState } from "react";

import type {
  VisualReviewAction,
  VisualReviewState,
} from "@/lib/visual-bible-api";
import { cn } from "@/lib/utils";

/** Mirror of the backend LEGAL_REVIEW_TRANSITIONS — server is the final judge. */
export const LEGAL_VISUAL_REVIEW_ACTIONS: Record<
  VisualReviewState,
  VisualReviewAction[]
> = {
  candidate: ["approve", "reject", "edit", "supersede", "needs_relink"],
  needs_relink: ["approve", "reject", "edit", "supersede"],
  approved: ["supersede", "needs_relink"],
  rejected: ["edit", "supersede"],
  superseded: [],
};

export const REVIEW_ACTION_LABEL_TEXT: Record<VisualReviewAction, string> = {
  approve: "批准",
  reject: "拒绝",
  edit: "编辑",
  supersede: "取代",
  needs_relink: "需要重新关联",
};

export type VisualReviewActionsProps = {
  reviewState: VisualReviewState;
  onReview: (action: VisualReviewAction, reason?: string) => void | Promise<void>;
  disabled?: boolean;
  className?: string;
};

export function VisualReviewActions({
  reviewState,
  onReview,
  disabled,
  className,
}: VisualReviewActionsProps) {
  const actions = LEGAL_VISUAL_REVIEW_ACTIONS[reviewState] ?? [];
  const [reason, setReason] = useState("");

  if (actions.length === 0) {
    return (
      <p
        data-testid="visual-bible-review-locked"
        className={cn("text-[11px] text-muted-foreground", className)}
      >
        该状态不允许进一步审查操作
      </p>
    );
  }

  return (
    <div
      data-testid="visual-bible-review-actions"
      data-state={reviewState}
      className={cn("flex flex-wrap items-center gap-1.5", className)}
    >
      {actions.map((action) => (
        <button
          key={action}
          type="button"
          data-testid={`visual-bible-review-action-${action}`}
          data-action={action}
          disabled={disabled}
          className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-foreground transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => onReview(action, reason.trim() || undefined)}
        >
          {REVIEW_ACTION_LABEL_TEXT[action]}
        </button>
      ))}
      <label className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
        <span>备注</span>
        <input
          data-testid="visual-bible-review-reason"
          type="text"
          value={reason}
          disabled={disabled}
          placeholder="可选审查备注"
          onChange={(event) => setReason(event.target.value)}
          className="h-6 w-40 rounded-md border border-border bg-background px-1.5 text-[11px] text-foreground placeholder:text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-50"
        />
      </label>
    </div>
  );
}
