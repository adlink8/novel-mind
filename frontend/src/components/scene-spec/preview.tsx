"use client";

/**
 * Phase 32 — Scene Spec / Prompt preview workspace (REQ-VIS-03, D-32-01..D-32-04).
 *
 * Renders the server-compiled candidate side-by-side:
 *
 * - the canonical SceneSpec (evidence-bounded details, negative constraints,
 *   explicit uncertainties) plus its candidate review state;
 * - every detail carries its provenance: evidence keys, Visual Bible stable
 *   ids, or an explicit user_interpretation author/rationale — the client
 *   never upgrades interpretation to canon (D-32-02);
 * - unsupported / future-spoiler material is shown as rejected or unresolved,
 *   never disguised as canon (fail closed);
 * - the provider prompt is displayed only via the server's `redacted_preview`;
 *   the browser never assembles a provider prompt from spec text (D-32-01).
 *
 * The data-fetching wrapper accepts injectable loaders so component tests can
 * drive error/partial/empty states without a backend.
 */

import { useEffect, useState } from "react";

import type {
  PromptArtifactView,
  PromptCompileRequest,
  SceneDetailView,
  SceneSpecDetailResponse,
  SpecReviewState,
  SpecSource,
} from "@/lib/scene-spec-api";
import { promptRevisionsApi, sceneSpecsApi } from "@/lib/scene-spec-api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Labels / badges
// ---------------------------------------------------------------------------

export const SPEC_REVIEW_STATE_LABEL_TEXT: Record<SpecReviewState, string> = {
  candidate: "候选 · 待审批",
  approved: "已批准",
  rejected: "已拒绝",
  superseded: "已被取代",
  needs_relink: "需要重新关联",
};

export const SPEC_REVIEW_STATE_BADGE_CLASS: Record<SpecReviewState, string> = {
  candidate: "border-amber-500/40 bg-amber-500/10 text-amber-800",
  approved: "border-emerald-500/40 bg-emerald-500/10 text-emerald-800",
  rejected: "border-rose-500/40 bg-rose-500/10 text-rose-800",
  superseded: "border-border bg-muted text-muted-foreground",
  needs_relink: "border-orange-500/40 bg-orange-500/10 text-orange-800",
};

export function SpecReviewStateBadge({ state }: { state: SpecReviewState }) {
  return (
    <span
      data-testid="scene-spec-review-state"
      data-state={state}
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
        SPEC_REVIEW_STATE_BADGE_CLASS[state]
      )}
    >
      {SPEC_REVIEW_STATE_LABEL_TEXT[state]}
    </span>
  );
}

export const SPEC_SOURCE_LABEL_TEXT: Record<SpecSource, string> = {
  evidence: "证据",
  visual_bible: "视觉圣经",
  user_interpretation: "用户解读",
};

export const SPEC_SOURCE_BADGE_CLASS: Record<SpecSource, string> = {
  evidence: "border-emerald-500/40 bg-emerald-500/10 text-emerald-800",
  visual_bible: "border-sky-500/40 bg-sky-500/10 text-sky-800",
  user_interpretation: "border-rose-500/40 bg-rose-500/10 text-rose-800",
};

export function SpecSourceBadge({ source }: { source: SpecSource }) {
  return (
    <span
      data-testid="scene-spec-detail-source"
      data-source={source}
      className={cn(
        "inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
        SPEC_SOURCE_BADGE_CLASS[source]
      )}
    >
      {SPEC_SOURCE_LABEL_TEXT[source]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Detail provenance (evidence / Visual Bible / interpretation)
// ---------------------------------------------------------------------------

function DetailProvenance({ detail }: { detail: SceneDetailView }) {
  const hasEvidence = (detail.evidence_keys ?? []).length > 0;
  const hasVisualBible = (detail.visual_bible_stable_ids ?? []).length > 0;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1">
      {detail.source === "evidence" && !hasEvidence ? (
        <span
          data-testid="scene-spec-detail-unresolved"
          className="rounded-full border border-rose-500/40 bg-rose-500/5 px-1.5 py-0.5 text-[10px] text-rose-800"
        >
          未通过验证，不可审批
        </span>
      ) : null}
      {detail.evidence_keys.map((key) => (
        <span
          key={key}
          data-testid="scene-spec-detail-evidence"
          className="rounded-full border border-emerald-500/40 bg-emerald-500/5 px-1.5 py-0.5 font-mono text-[10px] text-emerald-800"
        >
          证据 {key}
        </span>
      ))}
      {detail.visual_bible_stable_ids.map((stableId) => (
        <span
          key={stableId}
          data-testid="scene-spec-detail-visual-bible"
          className="rounded-full border border-sky-500/40 bg-sky-500/5 px-1.5 py-0.5 font-mono text-[10px] text-sky-800"
        >
          视觉圣经 {stableId}
        </span>
      ))}
      {detail.source === "user_interpretation" && (detail.author || detail.rationale) ? (
        <span
          data-testid="scene-spec-detail-rationale"
          className="text-[10px] text-muted-foreground"
        >
          {detail.author ? `作者：${detail.author} · ` : ""}
          {detail.rationale ?? ""}
        </span>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fetching workspace wrapper (owner/novel/spec scoped)
// ---------------------------------------------------------------------------

export type SceneSpecPreviewProps = {
  novelId: string | number;
  specId: number;
  /** Client-supplied prompt_key label for the preview compile (never persisted). */
  promptKey?: string;
  specLoader?: (
    novelId: string | number,
    specId: number
  ) => Promise<SceneSpecDetailResponse>;
  promptPreviewLoader?: (
    novelId: string | number,
    body: PromptCompileRequest
  ) => Promise<PromptArtifactView>;
  className?: string;
};

const DEFAULT_SPEC_LOADER: NonNullable<SceneSpecPreviewProps["specLoader"]> = (
  novelId,
  specId
) => sceneSpecsApi.getSpec(novelId, specId).then((res) => res.data);

const DEFAULT_PREVIEW_LOADER: NonNullable<
  SceneSpecPreviewProps["promptPreviewLoader"]
> = (novelId, body) =>
  promptRevisionsApi.preview(novelId, body).then((res) => res.data);

export function SceneSpecPreview({
  novelId,
  specId,
  promptKey,
  specLoader = DEFAULT_SPEC_LOADER,
  promptPreviewLoader = DEFAULT_PREVIEW_LOADER,
  className,
}: SceneSpecPreviewProps) {
  const [detail, setDetail] = useState<SceneSpecDetailResponse | null>(null);
  const [preview, setPreview] = useState<PromptArtifactView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const specKey = `preview-spec-${specId}-${Date.now()}`;
        const promptKeyValue = promptKey ?? `preview-prompt-${specId}-${Date.now()}`;
        const [specResult, promptResult] = await Promise.all([
          specLoader(novelId, specId),
          promptPreviewLoader(novelId, {
            spec_id: specId,
            prompt_key: promptKeyValue,
            adapter_id: "mock-provider",
          }),
        ]);
        if (cancelled) return;
        setDetail(specResult);
        setPreview(promptResult);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "加载 Scene Spec / Prompt 预览失败"
        );
        setDetail(null);
        setPreview(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelId, specId, promptKey]);

  if (loading) {
    return (
      <div
        data-testid="scene-spec-loading"
        className={cn("text-xs text-muted-foreground", className)}
      >
        正在加载 Scene Spec 候选…
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="scene-spec-error"
        className={cn(
          "rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-800",
          className
        )}
      >
        无法加载 Scene Spec 候选：{error}
      </div>
    );
  }

  if (!detail || !preview) return null;

  const spec = detail.spec;
  const isCanonActive = spec.review_state === "approved";
  const isEmpty =
    (spec.details ?? []).length === 0 &&
    (spec.negative_constraints ?? []).length === 0 &&
    (spec.uncertainties ?? []).length === 0;
  const futureSpoilers = (spec.uncertainties ?? []).filter(
    (item) => item.reason === "future_spoiler"
  );

  return (
    <div
      data-testid="scene-spec-preview"
      data-review-state={spec.review_state}
      className={cn("space-y-3", className)}
    >
      <header className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-3">
        <SpecReviewStateBadge state={spec.review_state} />
        <span className="text-xs font-medium">{spec.spec_key}</span>
        <span className="text-[10px] text-muted-foreground">
          修订 #{spec.revision_number}
        </span>
        <span className="text-[10px] text-muted-foreground">
          截止第 {spec.cutoff_chapter} 章
        </span>
        <span
          data-testid="scene-spec-source-snapshot"
          className="font-mono text-[10px] text-muted-foreground"
        >
          snapshot {spec.source_snapshot_id}
        </span>
      </header>

      {!isCanonActive ? (
        <div
          data-testid="scene-spec-candidate-only"
          className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-800"
        >
          候选规格 — 未经明确审批不得进入下游生成；预览不会触发生成。
        </div>
      ) : null}

      {detail.stale ? (
        <div
          data-testid="scene-spec-stale"
          className="rounded-lg border border-orange-500/40 bg-orange-500/5 px-2 py-1.5 text-[11px] text-orange-800"
        >
          该规格基于已过期的 Visual Bible / 源快照编译 — 静默复用已被拒绝。
        </div>
      ) : null}

      {isEmpty ? (
        <p
          data-testid="scene-spec-empty"
          className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground"
        >
          该规格没有可渲染的细节或约束 — 显示为空但不视为成功。
        </p>
      ) : (
        <div data-testid="scene-spec-detail-list" className="space-y-3">
          {(spec.details ?? []).length === 0 ? (
            <p data-testid="scene-spec-empty-details" className="text-xs text-muted-foreground">
              该规格暂无正向细节
            </p>
          ) : (
            <ul className="space-y-2">
              {spec.details.map((detail) => (
                <li
                  key={detail.detail_key}
                  data-testid="scene-spec-detail"
                  data-kind={detail.kind}
                  data-source={detail.source}
                  className="rounded-lg border border-border/60 bg-background/60 p-2"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="whitespace-pre-wrap text-xs leading-snug">
                      {detail.text}
                    </span>
                    <span className="shrink-0">
                      <SpecSourceBadge source={detail.source} />
                    </span>
                  </div>
                  <DetailProvenance detail={detail} />
                </li>
              ))}
            </ul>
          )}

          {(spec.negative_constraints ?? []).length > 0 ? (
            <div className="rounded-lg border border-border/60 bg-background/60 p-2">
              <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                负面约束（negative constraints）
              </p>
              <ul className="space-y-1">
                {spec.negative_constraints.map((constraint) => (
                  <li
                    key={constraint.constraint_key}
                    data-testid="scene-spec-constraint"
                    data-scope={constraint.scope}
                    className="text-[11px] text-foreground/85"
                  >
                    {constraint.scope}: {constraint.text}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {(spec.uncertainties ?? []).length > 0 ? (
            <div className="rounded-lg border border-border/60 bg-background/60 p-2">
              <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                未解析项（不视为正典）
              </p>
              <ul className="space-y-1">
                {spec.uncertainties.map((item) => (
                  <li
                    key={item.uncertainty_key}
                    data-testid="scene-spec-uncertainty"
                    data-reason={item.reason}
                    className="text-[11px] text-muted-foreground"
                  >
                    [{item.reason}] {item.detail}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}

      {futureSpoilers.length > 0 ? (
        <div
          data-testid="scene-spec-unsupported"
          className="rounded-lg border border-rose-500/40 bg-rose-500/5 px-2 py-1.5 text-[11px] text-rose-800"
        >
          存在未来剧透 / 无证据支持的细节：已标记为拒绝或未解析，不会进入正片。
        </div>
      ) : null}

      {/* The provider prompt is only ever the server's redacted preview; the
          browser never assembles a provider prompt from spec text (D-32-01). */}
      <div
        data-testid="scene-spec-prompt-preview"
        className="rounded-lg border border-border bg-card p-3"
      >
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <p className="text-[10px] font-medium text-muted-foreground">
            Prompt 预览（服务端编译 · 不触发生成）
          </p>
          <span
            data-testid="scene-spec-provider-calls"
            className="rounded-full border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
          >
            provider_calls: {preview.provider_calls}
          </span>
        </div>
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-background/60 p-2 font-mono text-[11px] leading-relaxed text-foreground/85">
          {preview.revision.redacted_preview ?? "(无服务端预览)"}
        </pre>
      </div>
    </div>
  );
}
