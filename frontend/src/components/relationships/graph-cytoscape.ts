/**
 * Cytoscape element / layout / stylesheet builders for the relationship graph.
 *
 * Converts app nodes/edges into cytoscape ElementDefinitions, builds the
 * hub-concentric layout options and the honesty-aware stylesheet, and applies
 * neighborhood focus classes. No React deps.
 */

import type {
  Core,
  ElementDefinition,
  LayoutOptions,
  NodeSingular,
} from "cytoscape";

import type {
  RelationshipGraphEdge,
  RelationshipGraphNode,
} from "@/lib/api";
import {
  edgeHonestyLabel,
  isProvisionalEdge,
} from "./relationship-honesty";
import { edgeElementId } from "./graph-layout";

export const EDGE_COLORS: Record<string, string> = {
  ally: "#4f6f52",
  enemy: "#b45309",
  family: "#6366f1",
  mentor: "#0e7490",
  romantic: "#be185d",
};

/** Provisional co-occurrence: slate dashed, not typed fiction colors. */
export const PROVISIONAL_EDGE_COLOR = "#94a3b8";

function edgeDisplayLabel(edge: RelationshipGraphEdge): string {
  return edgeHonestyLabel(edge);
}

export function buildElements(
  nodes: RelationshipGraphNode[],
  edges: RelationshipGraphEdge[],
  degree: Map<number, number>
): ElementDefinition[] {
  const maxDeg = Math.max(1, ...degree.values(), 1);
  const nodeEls: ElementDefinition[] = nodes.map((node) => {
    const d = degree.get(node.character_id) ?? 0;
    const size = 22 + Math.round((d / maxDeg) * 22);
    return {
      group: "nodes",
      data: {
        id: `n${node.character_id}`,
        characterId: node.character_id,
        label: node.name,
        degree: d,
        size,
      },
    };
  });
  const seen = new Set<string>();
  const edgeEls: ElementDefinition[] = [];
  for (const edge of edges) {
    const id = edgeElementId(edge);
    if (seen.has(id)) continue;
    seen.add(id);
    const provisional = isProvisionalEdge(edge);
    const w = Math.max(1, edge.evidence_count || 1);
    edgeEls.push({
      group: "edges",
      classes: provisional ? "provisional" : "accepted",
      data: {
        id,
        observationId: edge.observation_id,
        source: `n${edge.source_character_id}`,
        target: `n${edge.target_character_id}`,
        relationType: provisional ? "cooccur" : edge.relation_type,
        edgeKind: provisional
          ? "provisional_cooccurrence"
          : "accepted_observation",
        label: edgeDisplayLabel(edge),
        weight: w,
        width: Math.min(5, 1.2 + Math.log2(w + 1)),
      },
    });
  }
  return [...nodeEls, ...edgeEls];
}

/** Hub center, rings by hop distance — writing-tool character map. */
export function layoutHubConcentric(
  levels: Map<string, number>,
  animate: boolean
): LayoutOptions {
  return {
    name: "concentric",
    animate,
    animationDuration: animate ? 560 : 0,
    animationEasing: "ease-out-cubic",
    fit: true,
    padding: 52,
    minNodeSpacing: 52,
    startAngle: (3 / 2) * Math.PI,
    sweep: 2 * Math.PI,
    clockwise: true,
    equidistant: false,
    concentric: (node: NodeSingular) => {
      const hop = levels.get(node.id()) ?? 50;
      return 1000 - hop * 100;
    },
    levelWidth: () => 1,
  } as LayoutOptions;
}

/**
 * Cytoscape stylesheet for the character map.
 * `preferLabels` toggles on-canvas labels (hidden for large graphs).
 */
export function buildStylesheet(
  preferLabels: boolean
): import("cytoscape").StylesheetJson {
  return [
    // Kill gray grab/selection chrome on the canvas itself.
    {
      selector: "core",
      // cytoscape 类型把 Core 样式属性全标为必填；只覆盖需要的几项，其余沿用运行时默认值。
      style: {
        "active-bg-opacity": 0,
        "active-bg-size": 0,
        "selection-box-opacity": 0,
        "selection-box-border-width": 0,
        "outside-texture-bg-opacity": 0,
      } as import("cytoscape").Css.Core,
    },
    {
      selector: "node",
      style: {
        "background-color": "#4f6f52",
        "background-opacity": 0.92,
        label: preferLabels ? "data(label)" : "",
        color: "#1c1917",
        "font-size": 12,
        "font-weight": 600,
        "text-valign": "bottom",
        "text-margin-y": 8,
        "text-max-width": "88px",
        "text-wrap": "ellipsis",
        width: "data(size)",
        height: "data(size)",
        "border-width": 2,
        "border-color": "#e7e5e4",
        // overlay-padding draws a soft gray halo on grab — keep tight.
        "overlay-padding": 0,
        "overlay-opacity": 0,
        "transition-property":
          "background-color, border-color, opacity, line-color",
        "transition-duration": 0.22,
      },
    },
    {
      selector: "node.hub",
      style: {
        "background-color": "#1c1917",
        "border-color": "#f59e0b",
        "border-width": 3,
        label: "data(label)",
        "font-size": 13,
      },
    },
    {
      selector: "node:selected",
      style: {
        "background-color": "#1c1917",
        label: "data(label)",
        "border-color": "#f59e0b",
        "border-width": 3,
      },
    },
    {
      selector: "node.highlighted",
      style: {
        label: "data(label)",
        "border-color": "#78716c",
        opacity: 1,
      },
    },
    {
      selector: "node.faded",
      style: {
        opacity: 0.18,
        label: "",
      },
    },
    {
      selector: "edge",
      style: {
        width: "data(width)",
        "line-color": "#a8a29e",
        "target-arrow-color": "#a8a29e",
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        "control-point-step-size": 40,
        "line-style": "solid" as const,
        label: preferLabels ? "data(label)" : "",
        "font-size": 10,
        color: "#57534e",
        "text-background-color": "#fafaf9",
        "text-background-opacity": 0.9,
        "text-background-padding": "2px",
        "text-rotation": "autorotate",
        opacity: 0.9,
        "overlay-opacity": 0,
        "overlay-padding": 0,
        "transition-property": "line-color, opacity, width",
        "transition-duration": 0.22,
      },
    },
    {
      selector: "edge:selected, edge.highlighted",
      style: {
        width: 3.5,
        opacity: 1,
        "line-color": "#b45309",
        "target-arrow-color": "#b45309",
        label: "data(label)",
      },
    },
    {
      selector: "edge.faded",
      style: {
        opacity: 0.08,
        label: "",
      },
    },
    // Accepted type colors (solid). Must win over base edge style.
    ...Object.entries(EDGE_COLORS).map(([type, color]) => ({
      selector: `edge.accepted[relationType = "${type}"]`,
      style: {
        "line-color": color,
        "target-arrow-color": color,
        "line-style": "solid" as const,
      },
    })),
    // Provisional co-occurrence: dashed slate, label「共现」— visually honest.
    {
      selector: 'edge.provisional, edge[relationType = "cooccur"]',
      style: {
        "line-color": PROVISIONAL_EDGE_COLOR,
        "target-arrow-color": PROVISIONAL_EDGE_COLOR,
        "line-style": "dashed" as const,
        "line-dash-pattern": [6, 4],
        opacity: 0.75,
        color: "#64748b",
        label: preferLabels ? "共现" : "",
      },
    },
    {
      selector:
        'edge.provisional:selected, edge.provisional.highlighted, edge[relationType = "cooccur"]:selected',
      style: {
        "line-color": "#64748b",
        "target-arrow-color": "#64748b",
        "line-style": "dashed" as const,
        opacity: 1,
        label: "共现",
      },
    },
  ];
}

export function safeAddClass(
  ele: {
    empty?: () => boolean;
    addClass?: (c: string) => void;
  } | null | undefined,
  cls: string
) {
  if (!ele || typeof ele.addClass !== "function") return;
  if (typeof ele.empty === "function" && ele.empty()) return;
  ele.addClass(cls);
}

export function applyNeighborhoodFocus(
  cy: Core,
  focusNodeId: string | null,
  focusEdgeId: string | null
) {
  try {
    cy.batch(() => {
      cy.elements().removeClass("faded highlighted hub");
      if (focusNodeId) {
        const node = cy.getElementById(focusNodeId);
        if (!node || (typeof node.empty === "function" && node.empty())) return;
        const neighborhood = node.closedNeighborhood?.() ?? node;
        cy.elements().difference?.(neighborhood)?.addClass?.("faded");
        neighborhood.addClass?.("highlighted");
        safeAddClass(node, "hub");
        return;
      }
      if (focusEdgeId) {
        const edge = cy.getElementById(focusEdgeId);
        if (!edge || (typeof edge.empty === "function" && edge.empty())) return;
        const nodes = edge.connectedNodes?.() ?? edge;
        const neighborhood = nodes.union?.(edge) ?? edge;
        cy.elements().difference?.(neighborhood)?.addClass?.("faded");
        neighborhood.addClass?.("highlighted");
      }
    });
  } catch {
    // Test doubles / partial cytoscape mocks — ignore focus styling.
  }
}
