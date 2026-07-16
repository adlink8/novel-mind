"use client";

import {
  CLUE_STATE_LABELS,
  type ClueState,
  type VisibleClue,
} from "@/lib/clue-api";
import { cn } from "@/lib/utils";

const STATE_CHIP: Record<ClueState, string> = {
  candidate: "border-slate-300 bg-slate-50 text-slate-700",
  active: "border-sky-300 bg-sky-50 text-sky-800",
  reinforced: "border-violet-300 bg-violet-50 text-violet-800",
  paid_off: "border-emerald-300 bg-emerald-50 text-emerald-800",
  dismissed: "border-zinc-300 bg-zinc-50 text-zinc-600",
};

const STATE_DOT: Record<ClueState, string> = {
  candidate: "bg-slate-400",
  active: "bg-sky-500",
  reinforced: "bg-violet-500",
  paid_off: "bg-emerald-500",
  dismissed: "bg-zinc-400",
};

type Props = {
  clue: VisibleClue;
  selected: boolean;
  onSelect: (logicalClueId: string) => void;
};

/** Plant chapter: prefer first_cue_chapter; fall back to narrative chapter only. */
export function resolvePlantChapter(clue: VisibleClue): number {
  if (clue.first_cue_chapter != null && clue.first_cue_chapter > 0) {
    return clue.first_cue_chapter;
  }
  return clue.narrative_chapter_number;
}

/**
 * Payoff chapter when server provides it. Never invent from state or evidence.
 * null / undefined / non-positive → hidden (spoiler-safe).
 */
export function resolvePayoffChapter(clue: VisibleClue): number | null {
  if (clue.payoff_chapter == null) return null;
  if (clue.payoff_chapter <= 0) return null;
  return clue.payoff_chapter;
}

/**
 * Relative bar positions for plant/payoff markers.
 * Single-point (no payoff): plant at ~18%. Both known: plant left, payoff right of plant.
 */
export function spanPositions(
  plant: number,
  payoff: number | null
): { plantPct: number; payoffPct: number | null } {
  if (payoff == null || payoff === plant) {
    return { plantPct: 18, payoffPct: payoff === plant ? 82 : null };
  }
  const lo = Math.min(plant, payoff);
  const hi = Math.max(plant, payoff);
  const span = hi - lo || 1;
  // Map into 12%–88% with padding so dots stay inside the track
  const pad = 12;
  const usable = 100 - pad * 2;
  const plantPct = pad + ((plant - lo) / span) * usable;
  const payoffPct = pad + ((payoff - lo) / span) * usable;
  return { plantPct, payoffPct };
}

export function ClueCard(props: Props) {
  const { clue, selected, onSelect } = props;
  const plant = resolvePlantChapter(clue);
  const payoff = resolvePayoffChapter(clue);
  const { plantPct, payoffPct } = spanPositions(plant, payoff);
  const summary = clue.summary?.trim() || null;
  const stateLabel = CLUE_STATE_LABELS[clue.derived_state] ?? clue.derived_state;

  const spanAria =
    payoff != null
      ? `埋设第${plant}章至兑现第${payoff}章`
      : `埋设第${plant}章，兑现章未公开`;

  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      aria-label={`线索 ${clue.title}，${stateLabel}，${spanAria}`}
      data-testid="clue-card"
      data-logical-id={clue.logical_clue_id}
      onClick={() => onSelect(clue.logical_clue_id)}
      className={cn(
        "flex w-full flex-col gap-2 rounded-2xl border px-4 py-3 text-left transition",
        selected
          ? "border-foreground bg-foreground text-background shadow-md"
          : "border-border bg-background hover:border-foreground/40"
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3
          className={cn(
            "min-w-0 flex-1 font-serif text-sm font-semibold leading-snug",
            selected ? "" : "text-foreground"
          )}
        >
          {clue.title}
        </h3>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
            selected
              ? "border-background/40 bg-background/15 text-background"
              : STATE_CHIP[clue.derived_state]
          )}
          data-testid="clue-state-chip"
        >
          <span
            className={cn(
              "inline-block size-1.5 rounded-full",
              STATE_DOT[clue.derived_state],
              selected && "ring-1 ring-background"
            )}
            aria-hidden
          />
          {stateLabel}
        </span>
      </div>

      {summary && (
        <p
          className={cn(
            "line-clamp-2 text-xs leading-5",
            selected ? "opacity-80" : "text-muted-foreground"
          )}
          data-testid="clue-summary"
        >
          {summary}
        </p>
      )}

      {/* Plant → payoff span bar (not a timeline event strip) */}
      <div
        className="mt-0.5"
        data-testid="clue-span-bar"
        aria-label={spanAria}
      >
        <div
          className={cn(
            "relative h-2 rounded-full",
            selected ? "bg-background/25" : "bg-muted"
          )}
        >
          {payoffPct != null && (
            <span
              aria-hidden
              className={cn(
                "absolute top-1/2 h-0.5 -translate-y-1/2 rounded-full",
                selected ? "bg-background/80" : "bg-foreground/50"
              )}
              style={{
                left: `${Math.min(plantPct, payoffPct)}%`,
                width: `${Math.abs(payoffPct - plantPct)}%`,
              }}
            />
          )}
          <span
            aria-hidden
            className={cn(
              "absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2",
              selected
                ? "border-background bg-background"
                : "border-sky-600 bg-sky-500"
            )}
            style={{ left: `${plantPct}%` }}
            title={`埋设 第${plant}章`}
          />
          {payoffPct != null && (
            <span
              aria-hidden
              className={cn(
                "absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2",
                selected
                  ? "border-background bg-transparent"
                  : "border-emerald-600 bg-emerald-50"
              )}
              style={{ left: `${payoffPct}%` }}
              title={`兑现 第${payoff}章`}
            />
          )}
        </div>
        <div
          className={cn(
            "mt-1.5 flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5 text-[11px]",
            selected ? "opacity-80" : "text-muted-foreground"
          )}
        >
          <span data-testid="clue-plant-chapter">埋设 第{plant}章</span>
          {payoff != null ? (
            <span data-testid="clue-payoff-chapter">兑现 第{payoff}章</span>
          ) : (
            <span data-testid="clue-payoff-unknown">兑现未公开</span>
          )}
        </div>
      </div>

      <div
        className={cn(
          "flex flex-wrap gap-x-3 gap-y-0.5 text-[11px]",
          selected ? "opacity-75" : "text-muted-foreground"
        )}
      >
        {clue.evidence_count > 0 && (
          <span>证据 {clue.evidence_count}</span>
        )}
        {clue.link_count > 0 && <span>关联 {clue.link_count}</span>}
      </div>
    </button>
  );
}
