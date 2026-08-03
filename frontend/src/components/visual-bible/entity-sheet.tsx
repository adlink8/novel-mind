"use client";

/**
 * Phase 30 Visual Bible — Entity sheet workspace (REQ-VIS-01, D-30-01..D-30-04).
 *
 * Pulls the owner/novel/version candidate envelope from the server and renders
 * it for explicit human review:
 *
 * - entity sheets (character/place/item/faction/style) with visual description,
 *   the four-label authority badge, disclosure cutoff and per-claim evidence;
 * - a candidate-only banner and an explicit review state badge — a generated or
 *   unreviewed candidate is never shown as canon (D-30-01);
 * - append-only review history and an explicit review action bar; the browser
 *   only submits the action and the server decides the legal transition
 *   (D-30-04) — review truth is never saved client-side;
 * - evidence and reference-asset status rendered from the server envelope; the
 *   client never derives facts or recomputes candidates.
 *
 * The data-fetching wrapper accepts an injectable ``loader`` so component tests
 * can drive error/partial/empty states without a backend.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import type {
  VisualAuthority,
  VisualBibleVersionView,
  VisualEntityView,
  VisualReviewAction,
  VisualReviewState,
} from "@/lib/visual-bible-api";
import { visualBibleApi } from "@/lib/visual-bible-api";
import { cn } from "@/lib/utils";

import {
  EvidencePanel,
  type VisualEvidenceJumpTarget,
} from "./evidence-panel";
import { ReferenceAssetStatus } from "./reference-asset-status";
import {
  REVIEW_ACTION_LABEL_TEXT,
  VisualReviewActions,
} from "./review-actions";

// ---------------------------------------------------------------------------
// Labels / badges (four distinct authority labels, never collapsed)
// ---------------------------------------------------------------------------

export const AUTHORITY_LABEL_TEXT: Record<VisualAuthority, string> = {
  canon_fact: "正典事实",
  probable_inference: "可能推断",
  literary_interpretation: "文学解读",
  user_interpretation: "用户解读",
};

export const AUTHORITY_BADGE_CLASS: Record<VisualAuthority, string> = {
  canon_fact: "border-emerald-500/40 bg-emerald-500/10 text-emerald-800",
  probable_inference: "border-amber-500/40 bg-amber-500/10 text-amber-800",
  literary_interpretation:
    "border-violet-500/40 bg-violet-500/10 text-violet-800",
  user_interpretation: "border-rose-500/40 bg-rose-500/10 text-rose-800",
};

export function AuthorityBadge({ authority }: { authority: VisualAuthority }) {
  return (
    <span
      data-testid="visual-bible-authority-badge"
      data-authority={authority}
      className={cn(
        "inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
        AUTHORITY_BADGE_CLASS[authority]
      )}
    >
      {AUTHORITY_LABEL_TEXT[authority]}
    </span>
  );
}

export const REVIEW_STATE_LABEL_TEXT: Record<VisualReviewState, string> = {
  candidate: "候选 · 待审批",
  approved: "已批准",
  rejected: "已拒绝",
  superseded: "已被取代",
  needs_relink: "需要重新关联",
};

export const REVIEW_STATE_BADGE_CLASS: Record<VisualReviewState, string> = {
  candidate: "border-amber-500/40 bg-amber-500/10 text-amber-800",
  approved: "border-emerald-500/40 bg-emerald-500/10 text-emerald-800",
  rejected: "border-rose-500/40 bg-rose-500/10 text-rose-800",
  superseded: "border-border bg-muted text-muted-foreground",
  needs_relink: "border-orange-500/40 bg-orange-500/10 text-orange-800",
};

export function ReviewStateBadge({ state }: { state: VisualReviewState }) {
  return (
    <span
      data-testid="visual-bible-review-state-badge"
      data-state={state}
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
        REVIEW_STATE_BADGE_CLASS[state]
      )}
    >
      {REVIEW_STATE_LABEL_TEXT[state]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Entity sheet (presentational)
// ---------------------------------------------------------------------------

export type EntitySheetProps = {
  entity: VisualEntityView;
  novelId: string | number;
  onCitationNavigate?: (target: VisualEvidenceJumpTarget) => void;
  className?: string;
};

export function EntitySheet({
  entity,
  novelId,
  onCitationNavigate,
  className,
}: EntitySheetProps) {
  const claims = entity.claims ?? [];
  return (
    <article
      data-testid="visual-bible-entity"
      data-entity-key={entity.entity_key}
      data-entity-type={entity.entity_type}
      className={cn("rounded-xl border border-border bg-card p-3", className)}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{entity.entity_key}</h3>
        <span className="rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {entity.entity_type}
        </span>
        <AuthorityBadge authority={entity.authority} />
        <span
          data-testid="visual-bible-disclosure"
          className="text-[10px] text-muted-foreground"
        >
          披露截止：第 {entity.disclosure_cutoff} 章
        </span>
      </header>

      <p
        data-testid="visual-bible-entity-description"
        className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-foreground/90"
      >
        {entity.description}
      </p>

      {claims.length === 0 ? (
        <p
          data-testid="visual-bible-entity-no-claims"
          className="mt-2 text-[11px] text-muted-foreground"
        >
          该实体暂无视觉主张
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {claims.map((claim) => (
            <li
              key={claim.claim_key}
              data-testid="visual-bible-claim"
              data-claim-key={claim.claim_key}
              data-authority={claim.authority}
              className="rounded-lg border border-border/60 bg-background/60 p-2"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="whitespace-pre-wrap text-xs leading-snug">
                  {claim.description}
                </span>
                <span className="shrink-0">
                  <AuthorityBadge authority={claim.authority} />
                </span>
              </div>
              {claim.authority !== "canon_fact" &&
              (claim.author || claim.rationale) ? (
                <div
                  data-testid="visual-bible-claim-rationale"
                  className="mt-1 text-[10px] text-muted-foreground"
                >
                  {claim.author ? `作者：${claim.author} · ` : ""}
                  {claim.rationale ?? ""}
                </div>
              ) : null}
              <div className="mt-1.5">
                <EvidencePanel
                  claim={claim}
                  novelId={novelId}
                  onCitationNavigate={onCitationNavigate}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Fetching workspace wrapper (owner/novel/version scoped envelope)
// ---------------------------------------------------------------------------

export type VisualBibleEntitySheetProps = {
  novelId: string | number;
  versionId: number;
  loader?: (
    novelId: string | number,
    versionId: number
  ) => Promise<VisualBibleVersionView>;
  onReview?: (
    action: VisualReviewAction,
    reason?: string
  ) => void | Promise<void>;
  onCitationNavigate?: (target: VisualEvidenceJumpTarget) => void;
  className?: string;
};

const DEFAULT_LOADER: NonNullable<VisualBibleEntitySheetProps["loader"]> = (
  novelId,
  versionId
) => visualBibleApi.getVersion(novelId, versionId).then((res) => res.data);

export function VisualBibleEntitySheet({
  novelId,
  versionId,
  loader = DEFAULT_LOADER,
  onReview,
  onCitationNavigate,
  className,
}: VisualBibleEntitySheetProps) {
  const router = useRouter();
  const [version, setVersion] = useState<VisualBibleVersionView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await loader(novelId, versionId);
      setVersion(next);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "加载 Visual Bible 候选失败"
      );
      setVersion(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelId, versionId]);

  const handleReview = async (action: VisualReviewAction, reason?: string) => {
    if (!version || submitting) return;
    if (onReview) {
      await onReview(action, reason);
      return;
    }
    setSubmitting(true);
    try {
      await visualBibleApi.review(novelId, version.id, {
        action,
        actor_source: "human",
        actor: "owner",
        reason: reason
          ? `人工审查：${REVIEW_ACTION_LABEL_TEXT[action]} — ${reason}`
          : `人工审查：${REVIEW_ACTION_LABEL_TEXT[action]}`,
        event_key: `vb-${version.id}-${action}-${Date.now()}`,
        from_review_state: version.review_state,
      });
      await load();
    } finally {
      setSubmitting(false);
    }
  };

  const handleNavigate = (target: VisualEvidenceJumpTarget) => {
    if (onCitationNavigate) {
      onCitationNavigate(target);
      return;
    }
    const params = new URLSearchParams();
    params.set("chapter", String(target.chapter_id));
    params.set("start", String(target.source_start));
    params.set("from", "visual-bible");
    router.push(`/novels/${novelId}?${params.toString()}`);
  };

  if (loading) {
    return (
      <div
        data-testid="visual-bible-loading"
        className={cn("text-xs text-muted-foreground", className)}
      >
        正在加载 Visual Bible 候选…
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="visual-bible-error"
        className={cn(
          "rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-800",
          className
        )}
      >
        无法加载 Visual Bible 候选：{error}
      </div>
    );
  }

  if (!version) return null;

  const entities = version.entities ?? [];
  const assets = version.reference_assets ?? [];
  const isEmpty = entities.length === 0 && assets.length === 0;
  const isCanonActive = version.review_state === "approved";

  return (
    <div
      data-testid="visual-bible-entity-sheet"
      data-review-state={version.review_state}
      className={cn("space-y-3", className)}
    >
      <header className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-3">
        <ReviewStateBadge state={version.review_state} />
        <span className="text-xs font-medium">{version.version_key}</span>
        <span className="text-[10px] text-muted-foreground">
          修订 #{version.revision_number}
        </span>
        <span className="text-[10px] text-muted-foreground">
          截止第 {version.cutoff_chapter} 章
        </span>
        <span
          data-testid="visual-bible-source-snapshot"
          className="font-mono text-[10px] text-muted-foreground"
        >
          snapshot {version.source_snapshot_id}
        </span>
      </header>

      {!isCanonActive ? (
        <div
          data-testid="visual-bible-candidate-only"
          className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-800"
        >
          候选版本 — 未经明确审批不得进入正典；生成的视觉内容不会静默成为 canon。
        </div>
      ) : null}

      <VisualReviewActions
        reviewState={version.review_state}
        onReview={handleReview}
        disabled={submitting}
      />

      {isEmpty ? (
        <p
          data-testid="visual-bible-empty"
          className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground"
        >
          该版本暂无实体或参考素材 — 显示为空但不视为成功。
        </p>
      ) : (
        <>
          {entities.length === 0 ? (
            <p
              data-testid="visual-bible-empty-entities"
              className="text-xs text-muted-foreground"
            >
              该版本暂无实体
            </p>
          ) : (
            <div data-testid="visual-bible-entity-list" className="space-y-3">
              {entities.map((entity) => (
                <EntitySheet
                  key={entity.stable_id}
                  entity={entity}
                  novelId={novelId}
                  onCitationNavigate={handleNavigate}
                />
              ))}
            </div>
          )}

          <ReferenceAssetStatus assets={assets} />
        </>
      )}

      {version.review_events.length > 0 ? (
        <div
          data-testid="visual-bible-review-history"
          className="rounded-lg border border-border bg-card p-2"
        >
          <p className="mb-1 text-[10px] font-medium text-muted-foreground">
            审查历史（append-only）
          </p>
          <ul className="space-y-1">
            {version.review_events.map((event) => (
              <li
                key={event.event_key}
                data-testid="visual-bible-review-event"
                data-action={event.action}
                className="text-[10px] text-muted-foreground"
              >
                {REVIEW_ACTION_LABEL_TEXT[event.action]} · {event.from_review_state}
                {" → "}
                {event.to_review_state} · {event.actor} — {event.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
