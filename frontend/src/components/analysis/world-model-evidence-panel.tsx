"use client";

/**
 * Phase 27-04 — World Model evidence panel (REQ-WM-04, D-01/D-02/D-05/D-06).
 *
 * Renders the serialized world projection contract (queryplan/contracts.py,
 * mirrored in lib/api.ts) that rides on the shared QueryPlan consumer view:
 *
 * - four distinct authority labels with a visible, color-coded badge — an
 *   inference / interpretation is never silently rendered as a canon fact
 *   (D-01, no silent upgrades);
 * - disclosure timing (known_at / disclosure_cutoff) next to each claim
 *   (D-05);
 * - leaf evidence jump for approved claims (D-07/D-08), reusing the shared
 *   CitationChip; non-leaf / candidate-only claims have no jump;
 * - user interpretation isolated into a separate "用户解读/覆盖" section
 *   (D-06) — never merged into the candidate items;
 * - candidate-only items are explicitly labeled and never promoted; there is
 *   no active-pointer / promotion / cutover UI (D-02).
 *
 * This panel is presentational: it never calls the backend and never writes.
 */

import { useRouter } from "next/navigation";

import {
  CitationChip,
  type CitationNavigateTarget,
} from "@/components/reader/reader-chat-panel";
import type { WorldProjectionView, WorldAuthorityLabel } from "@/lib/api";
import { cn } from "@/lib/utils";

const AUTHORITY_LABEL_TEXT: Record<WorldAuthorityLabel, string> = {
  canon_fact: "正典事实",
  probable_inference: "可能推断",
  literary_interpretation: "文学解读",
  user_interpretation: "用户解读",
};

const AUTHORITY_BADGE_CLASS: Record<WorldAuthorityLabel, string> = {
  canon_fact: "border-emerald-500/40 bg-emerald-500/10 text-emerald-800",
  probable_inference: "border-amber-500/40 bg-amber-500/10 text-amber-800",
  literary_interpretation:
    "border-violet-500/40 bg-violet-500/10 text-violet-800",
  user_interpretation: "border-rose-500/40 bg-rose-500/10 text-rose-800",
};

function AuthorityBadge({
  authority,
}: {
  authority: WorldAuthorityLabel;
}) {
  return (
    <span
      data-testid="world-model-authority-badge"
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

function DisclosureText({
  knownAt,
  disclosureCutoff,
}: {
  knownAt: number;
  disclosureCutoff: number;
}) {
  return (
    <span
      data-testid="world-model-disclosure"
      className="text-[10px] text-muted-foreground"
    >
      已知于第 {knownAt} 章 · 第 {disclosureCutoff} 章后披露
    </span>
  );
}

export type WorldModelEvidencePanelProps = {
  novelId: string;
  worldProjection?: WorldProjectionView | null;
  /** Optional override; defaults to the analysis-chat-panel jump convention. */
  onCitationNavigate?: (target: CitationNavigateTarget) => void;
  className?: string;
};

export function WorldModelEvidencePanel({
  novelId,
  worldProjection,
  onCitationNavigate,
  className,
}: WorldModelEvidencePanelProps) {
  const router = useRouter();

  const handleNavigate = (target: CitationNavigateTarget) => {
    if (onCitationNavigate) {
      onCitationNavigate(target);
      return;
    }
    const params = new URLSearchParams();
    params.set("chapter", String(target.chapter_id));
    params.set("start", String(target.source_start));
    params.set("from", "world-model");
    router.push(`/novels/${novelId}?${params.toString()}`);
  };

  if (!worldProjection) {
    return (
      <div
        data-testid="world-model-evidence-panel"
        className={cn("text-xs text-muted-foreground", className)}
      >
        <p data-testid="world-model-status-unavailable">
          世界模型投影未生成 — 当前问题未请求该维度
        </p>
      </div>
    );
  }

  const statusBadge =
    worldProjection.status === "available" ? (
      <span
        data-testid="world-model-status-available"
        className="inline-flex items-center rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-800"
      >
        可用 · 证据就绪
      </span>
    ) : worldProjection.status === "candidate_only" ? (
      <span
        data-testid="world-model-status-candidate-only"
        className="inline-flex items-center rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-800"
      >
        候选 · 待审批，未激活
      </span>
    ) : (
      <span
        data-testid="world-model-status-unavailable"
        className="inline-flex items-center rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
      >
        暂不可用 · 已弃权
      </span>
    );

  return (
    <div
      data-testid="world-model-evidence-panel"
      data-status={worldProjection.status}
      className={cn("space-y-2 text-xs", className)}
    >
      <div className="flex flex-wrap items-center gap-2">
        {statusBadge}
        <span className="text-[10px] text-muted-foreground">
          披露截止：第 {worldProjection.cutoff} 章
        </span>
        {worldProjection.authorities.map((authority) => (
          <AuthorityBadge key={authority} authority={authority} />
        ))}
      </div>

      {worldProjection.items.length === 0 &&
      worldProjection.overrides.length === 0 ? (
        <p
          data-testid="world-model-empty-abstained"
          className="text-muted-foreground"
        >
          {worldProjection.status === "unavailable"
            ? "当前披露截止点之前没有可见的世界投影 — 未编造任何内容。"
            : "暂无世界投影条目。"}
        </p>
      ) : null}

      {worldProjection.items.length > 0 ? (
        <ul data-testid="world-model-candidate-items" className="space-y-1.5">
          {worldProjection.items.map((item) => (
            <li
              key={item.claim_key}
              data-testid="world-model-item"
              data-approved={item.approved}
              className="rounded-lg border border-border/60 bg-card px-2 py-1.5"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="whitespace-pre-wrap leading-snug">
                  {item.proposition}
                </span>
                <span className="shrink-0">
                  <AuthorityBadge authority={item.authority} />
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                <DisclosureText
                  knownAt={item.known_at}
                  disclosureCutoff={item.disclosure_cutoff}
                />
                {item.approved ? (
                  <CitationChip
                    citation={{
                      block_id: item.claim_key,
                      evidence_key: item.evidence_key,
                      context_evidence_ref_id: 0,
                      chapter_id: item.chapter_id,
                      source_start: item.source_start,
                      source_end: item.source_end,
                    }}
                    onNavigate={handleNavigate}
                  />
                ) : (
                  <span
                    data-testid="world-model-candidate-only"
                    className="rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                  >
                    候选，未激活
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {worldProjection.overrides.length > 0 ? (
        <div
          data-testid="world-model-overrides"
          className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-2 py-1.5"
        >
          <p className="mb-1 text-[10px] font-medium text-rose-800">
            用户解读（保护性覆盖）— 与候选投影隔离，不进入正典事实
          </p>
          <ul className="space-y-1.5">
            {worldProjection.overrides.map((item) => (
              <li
                key={item.claim_key}
                data-testid="world-model-override-item"
                className="flex items-start justify-between gap-2"
              >
                <span className="whitespace-pre-wrap leading-snug">
                  {item.proposition}
                </span>
                <span className="shrink-0">
                  <AuthorityBadge authority={item.authority} />
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
