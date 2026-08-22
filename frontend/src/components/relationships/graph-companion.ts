/**
 * Companion-list item builder for the relationship graph.
 *
 * Derives the keyboard-accessible "人物关系键盘列表" items from the displayed
 * slice, degree map and hub id. Pure function — no React/cytoscape deps.
 */

import type {
  RelationshipGraphEdge,
  RelationshipGraphNode,
} from "@/lib/api";
import {
  edgeCompanionMeta,
  isProvisionalEdge,
} from "./relationship-honesty";
import { edgeElementId } from "./graph-layout";

export type GraphCompanionItem =
  | {
      key: string;
      kind: "node";
      characterId: number;
      observationId: undefined;
      label: string;
      meta: string;
      isHub: boolean;
      provisional: false;
    }
  | {
      key: string;
      kind: "edge";
      characterId: undefined;
      observationId: number;
      label: string;
      meta: string;
      isHub: false;
      provisional: boolean;
      transition: RelationshipGraphEdge["transition"];
    };

export function buildCompanionItems(
  nodes: RelationshipGraphNode[],
  edges: RelationshipGraphEdge[],
  degree: Map<number, number>,
  hubId: number | null
): GraphCompanionItem[] {
  const rankedNodes = [...nodes].sort(
    (a, b) => (degree.get(b.character_id) ?? 0) - (degree.get(a.character_id) ?? 0)
  );
  const nodeItems: GraphCompanionItem[] = rankedNodes.map((node) => ({
    key: `n${node.character_id}`,
    kind: "node",
    characterId: node.character_id,
    observationId: undefined,
    label: node.name,
    meta:
      node.character_id === hubId
        ? `中心人物 · 连接 ${degree.get(node.character_id) ?? 0}`
        : `连接 ${degree.get(node.character_id) ?? 0} · 首见第 ${node.first_visible_chapter} 章`,
    isHub: node.character_id === hubId,
    provisional: false,
  }));
  const nameOf = (id: number) =>
    nodes.find((n) => n.character_id === id)?.name ?? `#${id}`;
  const edgeItems: GraphCompanionItem[] = edges.map((edge) => ({
    key: edgeElementId(edge),
    kind: "edge",
    characterId: undefined,
    observationId: edge.observation_id,
    label: `${nameOf(edge.source_character_id)} → ${nameOf(edge.target_character_id)}`,
    meta: edgeCompanionMeta(edge),
    isHub: false,
    provisional: isProvisionalEdge(edge),
    transition: edge.transition,
  }));
  return [...nodeItems, ...edgeItems];
}
