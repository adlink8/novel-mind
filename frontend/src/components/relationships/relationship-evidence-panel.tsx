"use client";

import { useRef } from "react";
import Link from "next/link";
import { X } from "lucide-react";

import type {
  RelationshipEvidenceResponse,
  RelationshipGraphEdge,
  RelationshipGraphNode,
  RelationshipProvenance,
} from "@/lib/api";
import { useDismissableLayer } from "@/lib/use-dismissable-layer";
import { cn } from "@/lib/utils";
import { RELATION_LABELS } from "./relationship-controls";
import {
  isProvisionalEdge,
  nonEstablishTransitionLabel,
  TRANSITION_LABELS,
} from "./relationship-honesty";

type Props = {
  novelId: string;
  edge: RelationshipGraphEdge | null;
  nodesById: Map<number, RelationshipGraphNode>;
  evidence: RelationshipEvidenceResponse | null;
  loading?: boolean;
  error?: string;
  onClose: () => void;
};

function provenanceLabel(kind: RelationshipProvenance) {
  return kind === "manual" ? "人工修正" : "机器推断";
}

export function RelationshipEvidencePanel(props: Props) {
  const { edge, evidence, nodesById, novelId } = props;
  const layerRef = useRef<HTMLElement>(null);
  const open = edge != null;
  const { present, closing } = useDismissableLayer({
    open,
    onDismiss: props.onClose,
    layerRef,
    closeOnOutside: false, // backdrop owns outside
  });

  if (!edge || !present) return null;

  const provisional = isProvisionalEdge(edge);
  const sourceName =
    nodesById.get(edge.source_character_id)?.name ??
    `人物 #${edge.source_character_id}`;
  const targetName =
    nodesById.get(edge.target_character_id)?.name ??
    `人物 #${edge.target_character_id}`;
  const typeLabel = provisional
    ? "共现"
    : (RELATION_LABELS[edge.relation_type] ?? edge.relation_type);
  const suggestedLabel =
    provisional && edge.suggested_type
      ? (RELATION_LABELS[edge.suggested_type] ?? edge.suggested_type)
      : null;
  const transitionBadge = provisional
    ? null
    : nonEstablishTransitionLabel(edge.transition);
  const provenance = evidence?.provenance ?? edge.provenance;

  return (
    <>
      <button
        type="button"
        aria-label="关闭证据遮罩"
        className={cn(
          "fixed inset-0 z-40 bg-black/30 transition-[opacity] motion-duration-spatial motion-ease-enter",
          open && !closing ? "opacity-100" : "pointer-events-none opacity-0 motion-ease-exit"
        )}
        onClick={props.onClose}
      />
      <aside
        ref={layerRef}
        role="dialog"
        aria-modal="true"
        aria-label="关系证据"
        aria-hidden={closing || undefined}
        data-testid="relationship-evidence-panel"
        className={cn(
          "fixed bottom-0 right-0 top-0 z-50 flex w-full max-w-md flex-col border-l bg-background shadow-2xl transition-[opacity,transform] motion-duration-spatial motion-ease-enter",
          open && !closing
            ? "translate-x-0 opacity-100"
            : "pointer-events-none translate-x-6 opacity-0 motion-ease-exit"
        )}
      >
        <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
          <div className="min-w-0">
            <p
              className={cn(
                "text-xs font-semibold uppercase tracking-wider",
                provisional ? "text-slate-600" : "text-primary"
              )}
            >
              {typeLabel}
              {provisional
                ? " · 临时共现"
                : ` · ${provenanceLabel(provenance)}`}
            </p>
            <h2 className="mt-1 font-serif text-xl font-semibold leading-snug">
              {sourceName} → {targetName}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              有效章节 {edge.valid_from_chapter}
              {edge.valid_to_chapter != null
                ? ` – ${edge.valid_to_chapter}`
                : " 起"}
              {!provisional
                ? ` · 置信度 ${Math.round(edge.confidence * 100)}%`
                : ""}
            </p>
            {transitionBadge && (
              <p
                data-testid="relationship-transition-badge"
                data-transition={edge.transition}
                className="mt-2 inline-flex rounded-full border border-amber-300/80 bg-amber-50 px-2.5 py-0.5 text-[11px] font-medium text-amber-950"
              >
                生命周期 · {TRANSITION_LABELS[edge.transition]}
                {edge.transition === "change"
                  ? "（非初次建立）"
                  : edge.transition === "end"
                    ? "（已结束，通常不显示在活动图上）"
                    : ""}
              </p>
            )}
          </div>
          <button
            type="button"
            aria-label="关闭关系证据"
            className="shrink-0 rounded-full border p-2 transition-[background-color] motion-duration-fast motion-ease-enter hover:bg-muted"
            onClick={props.onClose}
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {provisional && (
            <p
              data-testid="relationship-evidence-provisional-note"
              className="rounded-xl border border-slate-300/80 bg-slate-50 p-3 text-sm leading-6 text-slate-800"
            >
              这是时间线<strong>人物共现</strong>线索，不是已确认的同盟/敌对等关系。
              {suggestedLabel
                ? ` 启发式提示类型「${suggestedLabel}」仅供参考，不可当作事实。`
                : " 尚未形成可断言的关系类型。"}
            </p>
          )}

          {edge.evidence_preview && (
            <p
              className={cn(
                "rounded-xl bg-muted/60 p-3 text-sm leading-6",
                provisional && "mt-3"
              )}
            >
              {edge.evidence_preview}
            </p>
          )}

          <p className="mt-4 text-xs font-medium text-muted-foreground">
            来源溯源
          </p>
          <p className="mt-1 text-sm">
            {provisional
              ? "时间线共现（未确认关系）"
              : provenanceLabel(provenance)}
          </p>

          <p className="mt-4 text-xs font-medium text-muted-foreground">
            证据定位
          </p>
          {props.loading && (
            <p className="mt-2 text-sm text-muted-foreground" role="status" aria-busy="true">
              加载证据…
            </p>
          )}
          {props.error && (
            <p role="alert" className="mt-2 text-sm text-destructive">
              {props.error}
            </p>
          )}
          {!props.loading && !props.error && evidence && (
            <ul className="mt-2 grid gap-2">
              {evidence.evidence.map((item) => (
                <li
                  key={`${item.evidence_id}-${item.chapter_id}-${item.source_start}`}
                  className="rounded-2xl border bg-card p-3 text-sm"
                >
                  <p className="text-xs text-muted-foreground">
                    章节 #{item.chapter_id} · 偏移 {item.source_start}–
                    {item.source_end}
                  </p>
                  {item.excerpt && (
                    <p className="mt-1 leading-6 text-muted-foreground">
                      {item.excerpt}
                    </p>
                  )}
                  <Link
                    href={`/novels/${novelId}?chapter=${item.chapter_id}&from=relationships`}
                    className="mt-2 inline-flex text-xs font-medium text-primary underline-offset-2 hover:underline"
                  >
                    跳转章节阅读
                  </Link>
                </li>
              ))}
              {evidence.evidence.length === 0 && (
                <li className="text-sm text-muted-foreground">
                  {provisional
                    ? "共现线索暂无细粒度证据条目，请结合事件时间线核对。"
                    : "当前可见范围内无证据条目。"}
                </li>
              )}
            </ul>
          )}
          {!props.loading && !evidence && !props.error && (
            <p className="mt-2 text-sm text-muted-foreground">
              选择边后加载证据。
            </p>
          )}
        </div>
      </aside>
    </>
  );
}
