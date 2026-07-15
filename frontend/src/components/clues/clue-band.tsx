"use client";

import {
  CLUE_STATE_LABELS,
  type CluePayoffChainItem,
  type ClueState,
  type VisibleClue,
} from "@/lib/clue-api";

const STATE_DOT: Record<ClueState, string> = {
  candidate: "bg-slate-400",
  active: "bg-sky-500",
  reinforced: "bg-violet-500",
  paid_off: "bg-emerald-500",
  dismissed: "bg-zinc-400",
};

type Props = {
  clues: VisibleClue[];
  selectedId: string | null;
  onSelect: (logicalClueId: string) => void;
  /** Server-provided payoff chain for the selected clue; never inferred. */
  payoffChain?: CluePayoffChainItem[] | null;
  listExpanded?: boolean;
  onToggleList?: () => void;
};

export function ClueBand(props: Props) {
  const { clues, selectedId, onSelect } = props;

  return (
    <div className="grid gap-3" data-testid="clue-band">
      {/* Horizontal narrative band */}
      <div
        className="overflow-x-auto rounded-3xl border bg-card/70 p-4"
        role="region"
        aria-label="线索时间带"
      >
        {clues.length === 0 ? (
          <p className="text-sm text-muted-foreground">当前可见范围内没有线索。</p>
        ) : (
          <ol className="flex min-w-max items-stretch gap-0">
            {clues.map((clue, index) => {
              const selected = clue.logical_clue_id === selectedId;
              return (
                <li key={clue.logical_clue_id} className="flex items-center">
                  {index > 0 && (
                    <span
                      aria-hidden
                      className="mx-1 h-0.5 w-6 shrink-0 bg-border sm:w-10"
                    />
                  )}
                  <button
                    type="button"
                    aria-pressed={selected}
                    aria-label={`线索 ${clue.title}，第${clue.narrative_chapter_number}章，${CLUE_STATE_LABELS[clue.derived_state]}`}
                    onClick={() => onSelect(clue.logical_clue_id)}
                    className={`flex w-40 flex-col gap-1 rounded-2xl border px-3 py-3 text-left transition sm:w-48 ${
                      selected
                        ? "border-foreground bg-foreground text-background shadow-md"
                        : "border-border bg-background hover:border-foreground/40"
                    }`}
                  >
                    <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide opacity-80">
                      <span
                        className={`inline-block size-2 rounded-full ${STATE_DOT[clue.derived_state]} ${
                          selected ? "ring-1 ring-background" : ""
                        }`}
                        aria-hidden
                      />
                      第{clue.narrative_chapter_number}章
                    </span>
                    <span className="line-clamp-2 font-serif text-sm font-semibold leading-snug">
                      {clue.title}
                    </span>
                    <span className="text-[11px] opacity-75">
                      {CLUE_STATE_LABELS[clue.derived_state]}
                      {clue.evidence_count > 0
                        ? ` · 证据 ${clue.evidence_count}`
                        : ""}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </div>

      {/* Server payoff chain for selected clue */}
      {props.payoffChain && props.payoffChain.length > 0 && (
        <div
          className="rounded-2xl border border-emerald-300/60 bg-emerald-50/80 px-4 py-3"
          data-testid="clue-payoff-chain"
          aria-label="回收链"
        >
          <p className="text-xs font-medium text-emerald-950">回收链（服务端）</p>
          <ol className="mt-2 flex flex-wrap items-center gap-2 text-sm text-emerald-950">
            {props.payoffChain.map((step, i) => (
              <li key={`${step.event_key}-${i}`} className="flex items-center gap-2">
                {i > 0 && <span aria-hidden className="text-emerald-600">→</span>}
                <span className="rounded-full border border-emerald-400/70 bg-white px-2.5 py-0.5 text-xs">
                  {CLUE_STATE_LABELS[step.to_status as ClueState] ?? step.to_status}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Accessible companion list — same IDs, same order */}
      <div className="rounded-2xl border bg-card/60">
        <div className="flex items-center justify-between border-b px-4 py-2">
          <h2 className="text-sm font-semibold">线索列表</h2>
          {props.onToggleList && (
            <button
              type="button"
              className="text-xs text-primary underline-offset-2 hover:underline"
              onClick={props.onToggleList}
            >
              {props.listExpanded ? "收起列表" : "展开全部列表"}
            </button>
          )}
        </div>
        {(props.listExpanded ?? true) && (
          <ul
            role="listbox"
            aria-label="线索键盘列表"
            data-testid="clue-keyboard-list"
            className="max-h-72 divide-y overflow-y-auto"
          >
            {clues.map((clue) => {
              const selected = clue.logical_clue_id === selectedId;
              return (
                <li key={clue.logical_clue_id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={selected}
                    onClick={() => onSelect(clue.logical_clue_id)}
                    className={`flex w-full items-start gap-3 px-4 py-3 text-left text-sm ${
                      selected ? "bg-muted" : "hover:bg-muted/50"
                    }`}
                  >
                    <span className="mt-1 shrink-0 text-xs text-muted-foreground">
                      第{clue.narrative_chapter_number}章
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium">{clue.title}</span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {CLUE_STATE_LABELS[clue.derived_state]} · 偏移{" "}
                        {clue.source_start} · id {clue.logical_clue_id}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
            {clues.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted-foreground">
                无可见线索
              </li>
            )}
          </ul>
        )}
      </div>
    </div>
  );
}
