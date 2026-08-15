"use client";

/**
 * Phase 31 Key Scenes — human review workspace (REQ-VIS-02, D-31-01..D-31-05).
 *
 * Pulls the owner/novel/set candidate envelope from the server and renders it
 * for explicit human review:
 *
 * - candidate cards with evidence ranges, salience/diversity reasons, score
 *   breakdown, narrative coordinates, spoiler cutoff and detector/policy
 *   lineage (evidence is the only citation authority);
 * - a candidate-only banner and explicit review state — a candidate is never
 *   shown as canon, and future chapters beyond the cutoff are never requested;
 * - append-only review history and explicit per-candidate review actions; the
 *   browser only submits the action and the server decides the legal transition
 *   (D-31-04) — review truth is never saved client-side;
 * - an explicit freeze action that runs the server-side freeze gate and
 *   surfaces the frozen approved-subset manifest.
 *
 * The data-fetching wrapper accepts injectable ``loader``/``reviewAction``/
 * ``freezeAction`` so component tests can drive error/partial/empty states
 * without a backend.
 */

import axios from "axios";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import type {
  KeySceneFreezeRequest,
  KeySceneFreezeResponse,
  KeySceneReviewAction,
  KeySceneReviewRequest,
  KeySceneReviewResponse,
  SceneCandidateSetView,
  SceneCandidateView,
} from "@/lib/key-scenes-api";
import {
  diversityGroupLabel,
  keyScenesApi,
  shortKeySceneHash,
} from "@/lib/key-scenes-api";
import { cn } from "@/lib/utils";

import {
  ACTION_LABEL_TEXT,
  CandidateCard,
  REVIEW_STATE_LABEL_TEXT,
  type KeySceneJumpTarget,
} from "./candidate-card";

export type KeySceneReviewWorkspaceProps = {
  novelId: string | number;
  setId: number;
  loader?: (
    novelId: string | number,
    setId: number
  ) => Promise<SceneCandidateSetView>;
  reviewAction?: (
    novelId: string | number,
    setId: number,
    body: KeySceneReviewRequest
  ) => Promise<KeySceneReviewResponse>;
  freezeAction?: (
    novelId: string | number,
    setId: number,
    body: KeySceneFreezeRequest
  ) => Promise<KeySceneFreezeResponse>;
  onNavigate?: (target: KeySceneJumpTarget) => void;
  className?: string;
};

const DEFAULT_LOADER: NonNullable<KeySceneReviewWorkspaceProps["loader"]> = (
  novelId,
  setId
) => keyScenesApi.getSet(novelId, setId).then((res) => res.data);

export function KeySceneReviewWorkspace({
  novelId,
  setId,
  loader = DEFAULT_LOADER,
  reviewAction,
  freezeAction,
  onNavigate,
  className,
}: KeySceneReviewWorkspaceProps) {
  const router = useRouter();
  const [setView, setSetView] = useState<SceneCandidateSetView | null>(null);
  const [frozen, setFrozen] = useState<KeySceneFreezeResponse["frozen"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await loader(novelId, setId);
      setSetView(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载关键场景候选失败");
      setSetView(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // `load` 是异步数据加载；首个 setState（loading/error）在 effect 同步段内触发
    // react-hooks/set-state-in-effect 规则，但这是受控加载 pattern，行为是预期的。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelId, setId]);

  const handleReview = async (action: KeySceneReviewAction, candidateKey: string) => {
    if (!setView || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const candidate = setView.candidates.find(
        (c) => c.candidate_key === candidateKey
      );
      const body: KeySceneReviewRequest = {
        decision_key: `ks-${setId}-${candidateKey}-${action}-${Date.now()}`,
        action,
        actor_source: "human",
        actor: "owner",
        reason: `人工审查：${ACTION_LABEL_TEXT[action]}`,
        from_review_state: candidate?.review_state ?? "candidate",
        candidate_key: candidateKey,
      };
      if (reviewAction) {
        await reviewAction(novelId, setId, body);
      } else {
        await keyScenesApi.reviewCandidate(novelId, setId, body);
      }
      await load();
    } catch (err) {
      setError(
        axios.isAxiosError(err) && typeof err.response?.data?.detail === "string"
          ? err.response.data.detail
          : err instanceof Error
            ? err.message
            : "审查操作失败"
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleFreeze = async () => {
    if (!setView || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const body: KeySceneFreezeRequest = {
        actor_source: "human",
        actor: "owner",
        reason: "人工审查：冻结关键场景集",
      };
      const result = freezeAction
        ? await freezeAction(novelId, setId, body)
        : (await keyScenesApi.freeze(novelId, setId, body)).data;
      setFrozen(result.frozen);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "冻结失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleNavigate = (target: KeySceneJumpTarget) => {
    if (onNavigate) {
      onNavigate(target);
      return;
    }
    const params = new URLSearchParams();
    params.set("chapter", String(target.chapter_id));
    params.set("start", String(target.source_start));
    params.set("from", "key-scenes");
    router.push(`/novels/${novelId}?${params.toString()}`);
  };

  // Diversity grouping (deterministic key from the server envelope only).
  const groups = useMemo(() => {
    if (!setView) return [];
    const byKey = new Map<string, SceneCandidateView[]>();
    for (const candidate of setView.candidates) {
      const key = candidate.diversity_key || "unassigned";
      byKey.set(key, [...(byKey.get(key) ?? []), candidate]);
    }
    return [...byKey.entries()].map(([key, items]) => ({
      key,
      label: diversityGroupLabel(key),
      items,
    }));
  }, [setView]);

  if (loading) {
    return (
      <div
        data-testid="key-scene-loading"
        className={cn("text-xs text-muted-foreground", className)}
      >
        正在加载关键场景候选…
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="key-scene-error"
        className={cn(
          "rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-800",
          className
        )}
      >
        无法加载关键场景候选：{error}
      </div>
    );
  }

  if (!setView) return null;

  const approvedCount = setView.candidates.filter(
    (c) => c.review_state === "approved"
  ).length;
  const isFrozen = setView.review_state === "approved";

  return (
    <div
      data-testid="key-scene-workspace"
      data-review-state={setView.review_state}
      className={cn("space-y-3", className)}
    >
      <header className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-3">
        <span
          data-testid="key-scene-review-state"
          data-state={setView.review_state}
          className="rounded-full border px-2 py-0.5 text-[11px] font-medium"
        >
          {REVIEW_STATE_LABEL_TEXT[setView.review_state]}
        </span>
        <span className="text-xs font-medium">{setView.version_key}</span>
        <span className="text-[10px] text-muted-foreground">
          修订 #{setView.revision_number}
        </span>
        <span className="text-[10px] text-muted-foreground">
          截止第 {setView.cutoff_chapter} 章
        </span>
        <span
          data-testid="key-scene-source-snapshot"
          className="font-mono text-[10px] text-muted-foreground"
        >
          snapshot {setView.source_snapshot_id}
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">
          manifest {shortKeySceneHash(setView.manifest_hash)}
        </span>
      </header>

      {!isFrozen ? (
        <div
          data-testid="key-scene-candidate-only"
          className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-800"
        >
          候选场景集 — 未批准/未决候选不会进入冻结集或下游读者；模型提案与确定性评分/
          多样性/剧透校验及你的选择相互分离。
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="key-scene-freeze"
          disabled={submitting || approvedCount === 0}
          className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-[11px] font-medium text-emerald-800 transition-colors hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={handleFreeze}
        >
          {isFrozen ? "已冻结 · 重新读取" : "冻结关键场景集"}
        </button>
        {approvedCount === 0 && !isFrozen ? (
          <span className="text-[10px] text-muted-foreground">
            至少批准一个候选后才能冻结
          </span>
        ) : null}
        <span className="text-[10px] text-muted-foreground">
          已批准 {approvedCount} / {setView.candidates.length}
        </span>
      </div>

      {setView.candidates.length === 0 ? (
        <p
          data-testid="key-scene-empty"
          className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground"
        >
          该集合没有候选 — 显示为空但不视为成功。
        </p>
      ) : (
        <div data-testid="key-scene-group-list" className="space-y-3">
          {groups.map((group) => (
            <section
              key={group.key}
              data-testid="key-scene-group"
              className="space-y-2"
            >
              <h4 className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                <span
                  data-testid="key-scene-group-label"
                  className="font-mono"
                >
                  {group.label}
                </span>
                <span className="text-[10px]">多样性组 · {group.items.length} 个候选</span>
              </h4>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                {group.items.map((candidate) => (
                  <CandidateCard
                    key={candidate.candidate_key}
                    candidate={candidate}
                    onReview={handleReview}
                    onJump={handleNavigate}
                    disabled={submitting || isFrozen}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {frozen ? (
        <div
          data-testid="key-scene-frozen-manifest"
          className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2"
        >
          <p className="text-[10px] font-medium text-emerald-800">
            冻结清单（仅已批准候选）— manifest {shortKeySceneHash(frozen.manifest_hash)}
          </p>
          <ul className="mt-1 space-y-0.5">
            {frozen.candidates.map((candidate) => (
              <li
                key={candidate.candidate_key}
                data-testid="key-scene-frozen-candidate"
                className="text-[10px] text-muted-foreground"
              >
                第 {candidate.chapter_number} 章 · {candidate.scene_id} ·{" "}
                {REVIEW_STATE_LABEL_TEXT[candidate.review_state]}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {setView.review_decisions.length > 0 ? (
        <div
          data-testid="key-scene-review-history"
          className="rounded-lg border border-border bg-card p-2"
        >
          <p className="mb-1 text-[10px] font-medium text-muted-foreground">
            审查历史（append-only）
          </p>
          <ul className="space-y-1">
            {setView.review_decisions.map((decision) => (
              <li
                key={decision.decision_key}
                data-testid="key-scene-review-event"
                data-action={decision.action}
                className="text-[10px] text-muted-foreground"
              >
                {ACTION_LABEL_TEXT[decision.action]} · {decision.from_review_state}
                {" → "}
                {decision.to_review_state}
                {decision.candidate_key ? ` · ${decision.candidate_key}` : " · 集合"}
                {" · "}
                {decision.actor} — {decision.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
