"use client";

import type { TimelineOrdering } from "@/lib/api";

type Props = {
  ordering: TimelineOrdering;
  onOrderingChange: (value: TimelineOrdering) => void;
  people: string[];
  person: string;
  onPersonChange: (value: string) => void;
  causal: boolean;
  onCausalChange: (value: boolean) => void;
  fullBook: boolean;
  onFullBookRequest: (value: boolean) => void;
};

export function TimelineControls(props: Props) {
  return (
    <section
      aria-label="时间线控制"
      className="flex flex-wrap items-end gap-x-4 gap-y-2"
    >
      <div className="flex rounded-lg bg-muted/70 p-0.5" aria-label="时间线顺序">
        {([["narrative", "叙事顺序"], ["story", "故事时间"]] as const).map(
          ([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={props.ordering === value}
              onClick={() => props.onOrderingChange(value)}
              className={`rounded-md px-3 py-1.5 text-sm ${
                props.ordering === value
                  ? "bg-background font-medium text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </button>
          )
        )}
      </div>
      <label className="grid min-w-36 gap-1 text-xs text-muted-foreground">
        筛选人物
        <select
          value={props.person}
          onChange={(event) => props.onPersonChange(event.target.value)}
          className="h-9 rounded-lg border-0 bg-muted/50 px-2.5 text-sm text-foreground ring-1 ring-border/50 focus:outline-none focus:ring-foreground/20"
        >
          <option value="">全部人物</option>
          {props.people.map((person) => (
            <option key={person} value={person}>
              {person}
            </option>
          ))}
        </select>
      </label>
      <label className="flex h-9 items-center gap-2 text-sm text-muted-foreground">
        <input
          type="checkbox"
          checked={props.causal}
          onChange={(event) => props.onCausalChange(event.target.checked)}
          className="rounded border-border"
        />
        显示因果关系
      </label>
      <label className="flex h-9 items-center gap-2 text-sm text-amber-900/85">
        <input
          type="checkbox"
          checked={props.fullBook}
          onChange={(event) => props.onFullBookRequest(event.target.checked)}
          className="rounded border-border"
        />
        显示全书（可能剧透）
      </label>
    </section>
  );
}
