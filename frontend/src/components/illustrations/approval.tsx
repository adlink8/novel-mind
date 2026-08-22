"use client";

/**
 * Phase 33-04 Illustration — explicit human approval actions (REQ-VIS-04, D-33-03).
 *
 * The browser only ever submits one explicit review action; the server decides
 * the legal transition and returns the derived approval state. Review truth is
 * never saved client-side.
 *
 * - `ILLUSTRATION_LEGAL_ACTIONS` mirrors the backend transition map as a
 *   display hint only — the server remains the final judge.
 * - The component renders the legal action buttons for the current approval
 *   state, an optional audit reason, and the append-only review history.
 * - Actions stay candidate-only: approval from the UI never promotes a
 *   generated asset to canon by itself (Phase 34 owns publish).
 */

import { useState } from "react";

import type {
  IllustrationApprovalState,
  IllustrationReviewAction,
  IllustrationReviewEventView,
} from "@/lib/illustrations-api";
import { cn } from "@/lib/utils";

/** Mirror of the backend LEGAL_ILLUSTRATION_REVIEW_TRANSITIONS — display hint. */
export const ILLUSTRATION_LEGAL_ACTIONS: Record<
  IllustrationApprovalState,
  IllustrationReviewAction[]
> = {
  candidate: ["approve", "reject", "needs_relink"],
  proposal_ready: ["reject", "supersede", "needs_relink"],
  rejected: ["supersede", "needs_relink"],
  superseded: [],
};

export const ILLUSTRATION_STATE_LABEL_TEXT: Record<IllustrationApprovalState, string> = {
  candidate: "候选 · 待审批",
  proposal_ready: "提案就绪",
  rejected: "已拒绝",
  superseded: "已被取代",
};

export const ILLUSTRATION_ACTION_LABEL_TEXT: Record<IllustrationReviewAction, string> = {
  approve: "批准为提案",
  reject: "拒绝",
  supersede: "取代",
  needs_relink: "需要重新关联",
};

export type IllustrationApprovalActionsProps = {
  approvalState: IllustrationApprovalState;
  onReview: (
    action: IllustrationReviewAction,
    reason?: string
  ) => void | Promise<void>;
  disabled?: boolean;
  className?: string;
};

export function IllustrationApprovalActions({
  approvalState,
  onReview,
  disabled,
  className,
}: IllustrationApprovalActionsProps) {
  const actions = ILLUSTRATION_LEGAL_ACTIONS[approvalState] ?? [];
  const [reason, setReason] = useState("");

  if (actions.length === 0) {
    return (
      <p
        data-testid="illustration-approval-locked"
        className={cn("text-[11px] text-muted-foreground", className)}
      >
        该状态不允许进一步审查操作
      </p>
    );
  }

  return (
    <div
      data-testid="illustration-approval"
      data-state={approvalState}
      className={cn("flex flex-wrap items-center gap-1.5", className)}
    >
      {actions.map((action) => (
        <button
          key={action}
          type="button"
          data-testid={`illustration-approval-${action}`}
          data-action={action}
          disabled={disabled}
          className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-foreground transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => onReview(action, reason.trim() || undefined)}
        >
          {ILLUSTRATION_ACTION_LABEL_TEXT[action]}
        </button>
      ))}
      <label className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
        <span>备注</span>
        <input
          data-testid="illustration-approval-reason"
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

export type IllustrationReviewHistoryProps = {
  events: IllustrationReviewEventView[];
  className?: string;
};

export function IllustrationReviewHistory({
  events,
  className,
}: IllustrationReviewHistoryProps) {
  if (events.length === 0) return null;
  return (
    <div
      data-testid="illustration-review-history"
      className={cn("rounded-lg border border-border bg-card p-2", className)}
    >
      <p className="mb-1 text-[10px] font-medium text-muted-foreground">
        审查历史（append-only）
      </p>
      <ul className="space-y-1">
        {events.map((event) => (
          <li
            key={event.event_key}
            data-testid="illustration-review-event"
            data-action={event.action}
            className="text-[10px] text-muted-foreground"
          >
            {ILLUSTRATION_ACTION_LABEL_TEXT[event.action]} ·{" "}
            {event.from_approval_state} → {event.to_approval_state} ·{" "}
            {event.actor} — {event.reason}
          </li>
        ))}
      </ul>
    </div>
  );
}
