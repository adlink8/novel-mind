/**
 * Product honesty helpers for relationship graph edges.
 *
 * Phase 19: edge_kind / cooccur distinguishes accepted fact vs provisional
 * co-occurrence. This module adds transition honesty (change/end vs default
 * establish) and documents seed-path limits (ops backfill is not on graph API).
 */

import type { RelationshipGraphEdge } from "@/lib/api";
import { RELATION_LABELS } from "./relationship-controls";

export type RelationshipTransition = RelationshipGraphEdge["transition"];

/** Human labels for lifecycle transitions already returned by the graph API. */
export const TRANSITION_LABELS: Record<RelationshipTransition, string> = {
  establish: "建立",
  change: "变化",
  end: "结束",
};

/**
 * Badge text when transition is not the default establish path.
 * Returns null for establish (or unknown) so UI stays quiet on the common case.
 */
export function nonEstablishTransitionLabel(
  transition: RelationshipTransition | string | null | undefined
): string | null {
  if (transition == null || transition === "establish") return null;
  if (transition === "change" || transition === "end") {
    return TRANSITION_LABELS[transition];
  }
  return null;
}

export function isProvisionalEdge(edge: RelationshipGraphEdge): boolean {
  return (
    edge.edge_kind === "provisional_cooccurrence" ||
    edge.relation_type === "cooccur"
  );
}

/**
 * On-canvas / list type label. Provisional stays「共现」; accepted types use
 * RELATION_LABELS; non-establish transitions append a short honesty suffix.
 */
export function edgeHonestyLabel(edge: RelationshipGraphEdge): string {
  if (isProvisionalEdge(edge)) return "共现";
  const typeLabel =
    RELATION_LABELS[edge.relation_type] ?? edge.relation_type;
  const transitionLabel = nonEstablishTransitionLabel(edge.transition);
  if (!transitionLabel) return typeLabel;
  return `${typeLabel} · ${transitionLabel}`;
}

/**
 * Companion-list meta line: provisional honesty first; else type + optional
 * transition badge text for evolution edges.
 */
export function edgeCompanionMeta(edge: RelationshipGraphEdge): string {
  if (isProvisionalEdge(edge)) {
    const suggested =
      edge.suggested_type != null
        ? RELATION_LABELS[edge.suggested_type] ?? edge.suggested_type
        : null;
    return suggested ? `临时共现 · 提示${suggested}` : "临时共现";
  }
  return edgeHonestyLabel(edge);
}
