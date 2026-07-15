"use client";

import Link from "next/link";
import { X } from "lucide-react";

import type {
  RelationshipEvidenceResponse,
  RelationshipGraphEdge,
  RelationshipGraphNode,
  RelationshipProvenance,
} from "@/lib/api";
import { RELATION_LABELS } from "./relationship-controls";

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
  if (!edge) return null;

  const sourceName =
    nodesById.get(edge.source_character_id)?.name ??
    `人物 #${edge.source_character_id}`;
  const targetName =
    nodesById.get(edge.target_character_id)?.name ??
    `人物 #${edge.target_character_id}`;
  const typeLabel = RELATION_LABELS[edge.relation_type] ?? edge.relation_type;
  const provenance = evidence?.provenance ?? edge.provenance;

  return (
    <>
      <button
        type="button"
        aria-label="关闭证据遮罩"
        className="fixed inset-0 z-40 bg-black/30"
        onClick={props.onClose}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="关系证据"
        data-testid="relationship-evidence-panel"
        className="fixed bottom-0 right-0 top-0 z-50 flex w-full max-w-md flex-col border-l bg-background shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wider text-primary">
              {typeLabel} · {provenanceLabel(provenance)}
            </p>
            <h2 className="mt-1 font-serif text-xl font-semibold leading-snug">
              {sourceName} → {targetName}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              有效章节 {edge.valid_from_chapter}
              {edge.valid_to_chapter != null
                ? ` – ${edge.valid_to_chapter}`
                : " 起"}{" "}
              · 置信度 {Math.round(edge.confidence * 100)}%
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭关系证据"
            className="shrink-0 rounded-full border p-2"
            onClick={props.onClose}
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {edge.evidence_preview && (
            <p className="rounded-xl bg-muted/60 p-3 text-sm leading-6">
              {edge.evidence_preview}
            </p>
          )}

          <p className="mt-4 text-xs font-medium text-muted-foreground">
            来源溯源
          </p>
          <p className="mt-1 text-sm">{provenanceLabel(provenance)}</p>

          <p className="mt-4 text-xs font-medium text-muted-foreground">
            证据定位
          </p>
          {props.loading && (
            <p className="mt-2 text-sm text-muted-foreground">加载证据…</p>
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
                  key={item.evidence_id}
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
                  当前可见范围内无证据条目。
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
