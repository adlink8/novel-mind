"use client";

/**
 * Phase 31 Key Scenes — one candidate review card (REQ-VIS-02, D-31-02/04).
 *
 * Renders a candidate from the server envelope exactly as-is (no re-scoring,
 * no re-ranking, no browser-computed truth):
 *
 * - evidence range (chapter/range/hash/cutoff) with a source jump; evidence is
 *   the only citation authority (D-31-05 heuristic offsets are never shown as
 *   citation);
 * - salience reasons (closed vocabulary) + score breakdown + diversity key;
 * - narrative coordinates (cast/place/time/POV) and detector/policy lineage;
 * - the advisory speaker/dialogue heuristic metadata (availability/confidence/
 *   warnings) labelled as diagnostic, never as authority;
 * - explicit review actions (approve/reject/needs_relink) that only submit the
 *   action — the server decides the legal transition (D-31-04).
 */

import type {
  KeySceneReviewAction,
  SceneCandidateView,
  SceneEvidenceRangeView,
} from "@/lib/key-scenes-api";
import { cn } from "@/lib/utils";

export const REASON_LABEL_TEXT: Record<string, string> = {
  plot_turn: "剧情转折",
  emotional_peak: "情感高峰",
  character_salience: "角色凸显",
  visual_expressiveness: "视觉表现力",
  arc_impact: "弧线影响",
  quiet_emotional: "安静情感",
  dialogue_turn: "对话转折",
  repetition_penalty: "重复惩罚",
  diversity_quota: "多样性配额",
  ambiguity_warning: "歧义警告",
  detector_fallback: "检测回退",
  evidence_boundary: "证据边界",
  no_scene_boundaries: "无场景边界",
  malformed_range: "范围异常",
  beyond_cutoff: "超出截止",
};

export const REVIEW_STATE_LABEL_TEXT: Record<string, string> = {
  candidate: "候选 · 待审批",
  approved: "已批准",
  rejected: "已拒绝",
  superseded: "已被取代",
  needs_relink: "需要重新关联",
};

export const ACTION_LABEL_TEXT: Record<KeySceneReviewAction, string> = {
  approve: "批准",
  reject: "拒绝",
  needs_relink: "需要重新关联",
  supersede: "取代",
};

export type KeySceneJumpTarget = {
  chapter_id: number;
  source_start: number;
  source_end: number;
};

export type CandidateCardProps = {
  candidate: SceneCandidateView;
  onReview: (action: KeySceneReviewAction, candidateKey: string) => void | Promise<void>;
  onJump?: (target: KeySceneJumpTarget, candidate: SceneCandidateView) => void;
  disabled?: boolean;
  className?: string;
};

function EvidencePanel({
  evidence,
  onJump,
  candidate,
}: {
  evidence: SceneEvidenceRangeView;
  onJump?: (target: KeySceneJumpTarget, candidate: SceneCandidateView) => void;
  candidate: SceneCandidateView;
}) {
  return (
    <div
      data-testid="key-scene-evidence-panel"
      className="rounded-lg border border-border/60 bg-background/60 p-2"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted-foreground">
        <span>
          第 {evidence.chapter_number} 章 · 范围 {evidence.source_start}–
          {evidence.source_end}
        </span>
        <span
          data-testid="key-scene-evidence-hash"
          className="font-mono"
          title={evidence.content_hash}
        >
          hash {evidence.content_hash.slice(0, 8)}…
        </span>
        <span>截止第 {evidence.cutoff_chapter} 章</span>
        {onJump ? (
          <button
            type="button"
            data-testid="key-scene-evidence-jump"
            className="rounded-full border border-border bg-background px-1.5 py-0.5 text-[10px] text-foreground transition-colors hover:bg-muted"
            onClick={() => onJump(evidence, candidate)}
          >
            跳转原文
          </button>
        ) : null}
      </div>
      {evidence.excerpt ? (
        <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-[11px] leading-snug text-foreground/80">
          {evidence.excerpt}
        </p>
      ) : null}
    </div>
  );
}

export function CandidateCard({
  candidate,
  onReview,
  onJump,
  disabled,
  className,
}: CandidateCardProps) {
  const coordinates = candidate.coordinates ?? { cast: [] };
  const reasons = candidate.salience_reasons ?? [];
  const evidence = candidate.evidence_ranges ?? [];
  const signal = candidate.heuristic_signal;

  return (
    <article
      data-testid="key-scene-candidate"
      data-candidate-key={candidate.candidate_key}
      data-review-state={candidate.review_state}
      className={cn(
        "rounded-xl border border-border bg-card p-3",
        className
      )}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">
          第 {candidate.chapter_number} 章 · {candidate.scene_id}
        </h3>
        <span
          data-testid="key-scene-candidate-state"
          data-state={candidate.review_state}
          className={cn(
            "rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
            candidate.review_state === "approved"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-800"
              : candidate.review_state === "rejected"
                ? "border-rose-500/40 bg-rose-500/10 text-rose-800"
                : candidate.review_state === "needs_relink"
                  ? "border-orange-500/40 bg-orange-500/10 text-orange-800"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-800"
          )}
        >
          {REVIEW_STATE_LABEL_TEXT[candidate.review_state]}
        </span>
        <span
          data-testid="key-scene-score"
          className="text-[11px] font-medium text-muted-foreground"
        >
          评分 {candidate.score_total.toFixed(2)}
        </span>
      </header>

      {/* Narrative coordinates (cast/place/time/POV) */}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {(coordinates.cast ?? []).map((name) => (
          <span
            key={`cast-${name}`}
            data-testid="key-scene-coordinate"
            className="rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] text-foreground/80"
          >
            {name}
          </span>
        ))}
        {coordinates.place ? (
          <span className="rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] text-foreground/80">
            地点：{coordinates.place}
          </span>
        ) : null}
        {coordinates.time ? (
          <span className="rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] text-foreground/80">
            时间：{coordinates.time}
          </span>
        ) : null}
        {coordinates.pov ? (
          <span className="rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] text-foreground/80">
            POV：{coordinates.pov}
          </span>
        ) : null}
        <span
          data-testid="key-scene-diversity"
          className="rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] text-foreground/80"
          title={candidate.diversity_key}
        >
          多样性 {candidate.diversity_key.slice(0, 8)}…
        </span>
      </div>

      {/* Salience reasons (closed vocabulary) */}
      {reasons.length > 0 ? (
        <ul
          data-testid="key-scene-reasons"
          className="mt-2 space-y-1"
        >
          {reasons.map((reason) => (
            <li
              key={reason.reason_code}
              data-testid="key-scene-reason"
              data-reason-code={reason.reason_code}
              className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]"
            >
              <span className="font-medium text-foreground/90">
                {REASON_LABEL_TEXT[reason.reason_code] ?? reason.reason_code}
              </span>
              {typeof reason.score === "number" ? (
                <span className="text-[10px] text-muted-foreground">
                  {reason.score.toFixed(2)}
                </span>
              ) : null}
              {reason.detail ? (
                <span className="text-[10px] text-muted-foreground">
                  {reason.detail}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {/* Evidence is the only citation authority */}
      <div className="mt-2 space-y-1.5">
        {evidence.map((ref) => (
          <EvidencePanel
            key={ref.evidence_key}
            evidence={ref}
            candidate={candidate}
            onJump={onJump}
          />
        ))}
      </div>

      {/* Advisory heuristic metadata — diagnostic, never citation authority */}
      {signal ? (
        <div
          data-testid="key-scene-heuristic"
          data-availability={signal.availability}
          className="mt-2 rounded-lg border border-dashed border-border/70 bg-background/40 px-2 py-1.5 text-[10px] text-muted-foreground"
        >
          <span className="font-medium">
            {signal.availability === "available"
              ? "对话信号可用"
              : signal.availability === "ambiguous"
                ? "对话信号有歧义"
                : "对话信号不可用"}
          </span>
          {typeof signal.confidence === "number" ? (
            <span> · 置信度 {signal.confidence.toFixed(2)}</span>
          ) : null}
          {signal.warnings.length > 0 ? (
            <span> · {signal.warnings.join("；")}</span>
          ) : null}
          <span className="ml-1 rounded-full border border-border bg-muted px-1 py-0.5 text-[9px]">
            仅诊断，非证据
          </span>
        </div>
      ) : null}

      {/* Lineage */}
      <div className="mt-2 flex flex-wrap gap-x-2 gap-y-0.5 text-[9px] text-muted-foreground">
        <span>
          detector {candidate.detector_id}@{candidate.detector_version}
        </span>
        <span className="font-mono">policy {candidate.policy_hash.slice(0, 8)}…</span>
        <span className="font-mono">
          source {candidate.source_hash.slice(0, 8)}…
        </span>
      </div>

      {/* Explicit review actions — server decides the legal transition */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {candidate.review_state === "candidate" ||
        candidate.review_state === "needs_relink" ? (
          <>
            <button
              type="button"
              data-testid="key-scene-review-approve"
              disabled={disabled}
              className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-800 transition-colors hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => onReview("approve", candidate.candidate_key)}
            >
              {ACTION_LABEL_TEXT.approve}
            </button>
            <button
              type="button"
              data-testid="key-scene-review-reject"
              disabled={disabled}
              className="rounded-full border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-[11px] text-rose-800 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => onReview("reject", candidate.candidate_key)}
            >
              {ACTION_LABEL_TEXT.reject}
            </button>
            <button
              type="button"
              data-testid="key-scene-review-needs-relink"
              disabled={disabled}
              className="rounded-full border border-orange-500/40 bg-orange-500/10 px-2 py-0.5 text-[11px] text-orange-800 transition-colors hover:bg-orange-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => onReview("needs_relink", candidate.candidate_key)}
            >
              {ACTION_LABEL_TEXT.needs_relink}
            </button>
          </>
        ) : null}
      </div>
    </article>
  );
}
