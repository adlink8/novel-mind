"use client";

import {
  CLUE_STATE_LABELS,
  type CluePayoffChainItem,
  type ClueState,
  type VisibleClue,
} from "@/lib/clue-api";
import { ClueCard } from "./clue-card";

type Props = {
  clues: VisibleClue[];
  selectedId: string | null;
  onSelect: (logicalClueId: string) => void;
  /** Server-provided payoff chain for the selected clue; never inferred. */
  payoffChain?: CluePayoffChainItem[] | null;
  listExpanded?: boolean;
  onToggleList?: () => void;
};

/**
 * Primary clue presentation: vertical plant→payoff cards.
 * Demotes the old horizontal “event strip” so clues are not isomorphic to timeline.
 */
export function ClueBand(props: Props) {
  const { clues, selectedId, onSelect } = props;
  const expanded = props.listExpanded ?? true;

  return (
    <div className="grid gap-3" data-testid="clue-band">
      <div className="rounded-2xl border bg-card/60">
        <div className="flex items-center justify-between border-b px-4 py-2">
          <div>
            <h2 className="text-sm font-semibold">线索列表</h2>
            <p className="text-[11px] text-muted-foreground">
              按埋设→兑现跨度展示，非时间线事件流
            </p>
          </div>
          {props.onToggleList && (
            <button
              type="button"
              className="text-xs text-primary underline-offset-2 hover:underline"
              onClick={props.onToggleList}
            >
              {expanded ? "收起列表" : "展开全部列表"}
            </button>
          )}
        </div>

        {expanded && (
          <ul
            role="listbox"
            aria-label="线索列表"
            data-testid="clue-keyboard-list"
            className="grid max-h-[28rem] gap-2 overflow-y-auto p-3 sm:max-h-[32rem]"
          >
            {clues.map((clue) => (
              <li key={clue.logical_clue_id}>
                <ClueCard
                  clue={clue}
                  selected={clue.logical_clue_id === selectedId}
                  onSelect={onSelect}
                />
              </li>
            ))}
            {clues.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted-foreground">
                当前可见范围内没有线索。
              </li>
            )}
          </ul>
        )}

        {!expanded && clues.length > 0 && (
          <p className="px-4 py-3 text-xs text-muted-foreground">
            已收起 {clues.length} 条线索
          </p>
        )}
      </div>

      {/* Server payoff chain for selected clue — lifecycle only, not a timeline strip */}
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
                {i > 0 && (
                  <span aria-hidden className="text-emerald-600">
                    →
                  </span>
                )}
                <span className="rounded-full border border-emerald-400/70 bg-white px-2.5 py-0.5 text-xs">
                  {CLUE_STATE_LABELS[step.to_status as ClueState] ?? step.to_status}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
