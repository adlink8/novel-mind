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
    <section aria-label="时间线控制" className="flex flex-wrap items-end gap-3 rounded-2xl border bg-card/80 p-3">
      <div className="flex rounded-xl bg-muted p-1" aria-label="时间线顺序">
        {([['narrative', '叙事顺序'], ['story', '故事时间']] as const).map(([value, label]) => (
          <button key={value} type="button" aria-pressed={props.ordering === value} onClick={() => props.onOrderingChange(value)} className={`rounded-lg px-3 py-2 text-sm ${props.ordering === value ? 'bg-background font-semibold shadow-sm' : 'text-muted-foreground'}`}>
            {label}
          </button>
        ))}
      </div>
      <label className="grid min-w-40 gap-1 text-xs text-muted-foreground">
        筛选人物
        <select value={props.person} onChange={(event) => props.onPersonChange(event.target.value)} className="h-10 rounded-xl border bg-background px-3 text-sm text-foreground">
          <option value="">全部人物</option>
          {props.people.map((person) => <option key={person} value={person}>{person}</option>)}
        </select>
      </label>
      <label className="flex h-10 items-center gap-2 rounded-xl border px-3 text-sm">
        <input type="checkbox" checked={props.causal} onChange={(event) => props.onCausalChange(event.target.checked)} />
        显示因果关系
      </label>
      <label className="flex h-10 items-center gap-2 rounded-xl border border-amber-300/70 bg-amber-50 px-3 text-sm text-amber-950">
        <input type="checkbox" checked={props.fullBook} onChange={(event) => props.onFullBookRequest(event.target.checked)} />
        显示全书（可能剧透）
      </label>
    </section>
  );
}
