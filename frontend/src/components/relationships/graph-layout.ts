/**
 * Pure relationship-graph layout math.
 *
 * Display caps, degree/hub computation, and BFS ring-levels for the
 * Milanote-style concentric character map. No React / cytoscape deps —
 * unit-testable in isolation.
 */

import type {
  RelationshipGraphEdge,
  RelationshipGraphNode,
} from "@/lib/api";
import { isProvisionalEdge } from "./relationship-honesty";

/** Cap for on-canvas clarity (server may still return more for filters). */
export const DISPLAY_EDGE_CAP = 32;
export const DISPLAY_NODE_CAP = 28;

export function edgeElementId(edge: RelationshipGraphEdge): string {
  return `e${edge.source_character_id}-${edge.target_character_id}-${edge.observation_id}`;
}

export function degreeMap(edges: RelationshipGraphEdge[]): Map<number, number> {
  const degree = new Map<number, number>();
  for (const edge of edges) {
    degree.set(
      edge.source_character_id,
      (degree.get(edge.source_character_id) ?? 0) + 1
    );
    degree.set(
      edge.target_character_id,
      (degree.get(edge.target_character_id) ?? 0) + 1
    );
  }
  return degree;
}

/** Keep strongest edges + induced nodes for a readable character map.
 * Prefer accepted observations over provisional co-occurrence within the cap.
 */
export function displaySlice(
  nodes: RelationshipGraphNode[],
  edges: RelationshipGraphEdge[]
): { nodes: RelationshipGraphNode[]; edges: RelationshipGraphEdge[] } {
  if (edges.length <= DISPLAY_EDGE_CAP && nodes.length <= DISPLAY_NODE_CAP) {
    return { nodes, edges };
  }
  const ranked = [...edges].sort((a, b) => {
    const aProv = isProvisionalEdge(a) ? 1 : 0;
    const bProv = isProvisionalEdge(b) ? 1 : 0;
    // Accepted first, then higher evidence, then stable id.
    return (
      aProv - bProv ||
      (b.evidence_count || 0) - (a.evidence_count || 0) ||
      a.observation_id - b.observation_id
    );
  });
  const keptEdges = ranked.slice(0, DISPLAY_EDGE_CAP);
  const keepIds = new Set<number>();
  for (const e of keptEdges) {
    keepIds.add(e.source_character_id);
    keepIds.add(e.target_character_id);
  }
  // Prefer high-degree nodes if still over cap
  if (keepIds.size > DISPLAY_NODE_CAP) {
    const deg = degreeMap(keptEdges);
    const top = [...keepIds]
      .sort((a, b) => (deg.get(b) ?? 0) - (deg.get(a) ?? 0))
      .slice(0, DISPLAY_NODE_CAP);
    keepIds.clear();
    for (const id of top) keepIds.add(id);
  }
  const filteredEdges = keptEdges.filter(
    (e) =>
      keepIds.has(e.source_character_id) && keepIds.has(e.target_character_id)
  );
  const filteredNodes = nodes.filter((n) => keepIds.has(n.character_id));
  // Include any residual node mentioned only in edges
  const nodeIds = new Set(filteredNodes.map((n) => n.character_id));
  for (const id of keepIds) {
    if (!nodeIds.has(id)) {
      filteredNodes.push({
        character_id: id,
        name: `人物 #${id}`,
        aliases: [],
        first_visible_chapter: 1,
      });
    }
  }
  return { nodes: filteredNodes, edges: filteredEdges };
}

export function pickHubId(
  nodes: RelationshipGraphNode[],
  degree: Map<number, number>
): number | null {
  if (!nodes.length) return null;
  return [...nodes]
    .sort(
      (a, b) =>
        (degree.get(b.character_id) ?? 0) -
          (degree.get(a.character_id) ?? 0) ||
        a.first_visible_chapter - b.first_visible_chapter ||
        a.character_id - b.character_id
    )[0].character_id;
}

/** BFS hop distance from hub (Milanote-style rings). */
export function hopLevels(
  hubId: number | null,
  nodes: RelationshipGraphNode[],
  edges: RelationshipGraphEdge[]
): Map<string, number> {
  const levels = new Map<string, number>();
  if (hubId == null) {
    for (const n of nodes) levels.set(`n${n.character_id}`, 1);
    return levels;
  }
  const adj = new Map<number, number[]>();
  for (const n of nodes) adj.set(n.character_id, []);
  for (const e of edges) {
    adj.get(e.source_character_id)?.push(e.target_character_id);
    adj.get(e.target_character_id)?.push(e.source_character_id);
  }
  const queue = [hubId];
  levels.set(`n${hubId}`, 0);
  while (queue.length) {
    const cur = queue.shift()!;
    const depth = levels.get(`n${cur}`) ?? 0;
    for (const nxt of adj.get(cur) ?? []) {
      const key = `n${nxt}`;
      if (levels.has(key)) continue;
      levels.set(key, depth + 1);
      queue.push(nxt);
    }
  }
  for (const n of nodes) {
    const key = `n${n.character_id}`;
    if (!levels.has(key)) levels.set(key, 99);
  }
  return levels;
}
