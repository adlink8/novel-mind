"use client";

import {
  CLUE_STATE_LABELS,
  type ClueState,
  type ClueVersionSource,
  type ClueVersionView,
} from "@/lib/clue-api";

type Props = {
  envelope: { active: ClueVersionView | null; running_candidate: ClueVersionView | null };
  source: ClueVersionSource;
  onSourceChange: (source: "active" | "running_candidate") => void;
  statusFilter: ClueState | "";
  onStatusFilterChange: (value: ClueState | "") => void;
  characterId: number | "";
  onCharacterIdChange: (value: number | "") => void;
  /** Server-visible filter options from the selected view. */
  availableStates: ClueState[];
  availableCharacterIds: number[];
  counts: ClueVersionView["counts"] | null;
};

export function ClueControls(props: Props) {
  const { envelope, source, counts } = props;

  return (
    <section
      aria-label="线索筛选与版本"
      className="grid gap-2 rounded-2xl border bg-card/80 p-3"
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="grid min-w-36 gap-1 text-xs text-muted-foreground">
          状态筛选
          <select
            aria-label="筛选线索状态"
            value={props.statusFilter}
            onChange={(event) =>
              props.onStatusFilterChange(
                (event.target.value || "") as ClueState | ""
              )
            }
            className="h-10 rounded-xl border bg-background px-3 text-sm text-foreground"
          >
            <option value="">全部状态</option>
            {props.availableStates.map((state) => (
              <option key={state} value={state}>
                {CLUE_STATE_LABELS[state] ?? state}
                {counts?.by_state?.[state] != null
                  ? ` (${counts.by_state[state]})`
                  : ""}
              </option>
            ))}
          </select>
        </label>

        <label className="grid min-w-36 gap-1 text-xs text-muted-foreground">
          关联人物
          <select
            aria-label="筛选关联人物"
            value={props.characterId === "" ? "" : String(props.characterId)}
            onChange={(event) => {
              const raw = event.target.value;
              props.onCharacterIdChange(raw === "" ? "" : Number(raw));
            }}
            className="h-10 rounded-xl border bg-background px-3 text-sm text-foreground"
          >
            <option value="">全部人物</option>
            {props.availableCharacterIds.map((id) => (
              <option key={id} value={id}>
                人物 #{id}
              </option>
            ))}
          </select>
        </label>

        {counts && (
          <p className="pb-2 text-xs text-muted-foreground" data-testid="clue-counts">
            可见 {counts.clues} 条
            {Object.entries(counts.by_state ?? {})
              .map(
                ([state, n]) =>
                  ` · ${CLUE_STATE_LABELS[state as ClueState] ?? state} ${n}`
              )
              .join("")}
          </p>
        )}
      </div>

      {(envelope.active || envelope.running_candidate) && (
        <div
          role="tablist"
          aria-label="线索版本"
          className="flex gap-2 overflow-x-auto"
        >
          {envelope.active && (
            <button
              type="button"
              role="tab"
              aria-selected={source === "active"}
              onClick={() => props.onSourceChange("active")}
              className={`whitespace-nowrap rounded-full px-3 py-1.5 text-xs ${
                source === "active"
                  ? "bg-foreground text-background"
                  : "border bg-background"
              }`}
            >
              当前版本 · v{envelope.active.version_id}
            </button>
          )}
          {envelope.running_candidate && (
            <button
              type="button"
              role="tab"
              aria-selected={source === "running_candidate"}
              onClick={() => props.onSourceChange("running_candidate")}
              className={`whitespace-nowrap rounded-full px-3 py-1.5 text-xs ${
                source === "running_candidate"
                  ? "bg-foreground text-background"
                  : "border bg-background"
              }`}
            >
              正在生成 · v{envelope.running_candidate.version_id}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
