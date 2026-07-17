"use client";

/**
 * Cytoscape relationship graph.
 * Layout inspired by writing tools (Milanote / character maps):
 * hub character center → secondary ring → supporting outer; labeled links;
 * layout animation + neighborhood focus on select/hover.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  Core,
  ElementDefinition,
  EventObject,
  LayoutOptions,
  NodeSingular,
} from "cytoscape";

import type {
  GraphDegradationMode,
  RelationshipGraphEdge,
  RelationshipGraphNode,
} from "@/lib/api";
import {
  edgeCompanionMeta,
  edgeHonestyLabel,
  isProvisionalEdge,
} from "./relationship-honesty";

export type GraphSelection =
  | { kind: "node"; characterId: number }
  | { kind: "edge"; observationId: number }
  | null;

type Props = {
  nodes: RelationshipGraphNode[];
  edges: RelationshipGraphEdge[];
  mode: GraphDegradationMode;
  selected: GraphSelection;
  onSelect: (selection: GraphSelection) => void;
  onReady?: (api: {
    zoomIn: () => void;
    zoomOut: () => void;
    fit: () => void;
    destroy: () => void;
  }) => void;
};

const EDGE_COLORS: Record<string, string> = {
  ally: "#4f6f52",
  enemy: "#b45309",
  family: "#6366f1",
  mentor: "#0e7490",
  romantic: "#be185d",
};

/** Provisional co-occurrence: slate dashed, not typed fiction colors. */
const PROVISIONAL_EDGE_COLOR = "#94a3b8";

/** Cap for on-canvas clarity (server may still return more for filters). */
const DISPLAY_EDGE_CAP = 32;
const DISPLAY_NODE_CAP = 28;

function edgeElementId(edge: RelationshipGraphEdge): string {
  return `e${edge.source_character_id}-${edge.target_character_id}-${edge.observation_id}`;
}

// Re-export for existing tests / consumers.
export { isProvisionalEdge } from "./relationship-honesty";

function edgeDisplayLabel(edge: RelationshipGraphEdge): string {
  return edgeHonestyLabel(edge);
}

function degreeMap(edges: RelationshipGraphEdge[]): Map<number, number> {
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
function displaySlice(
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

function buildElements(
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

function pickHubId(
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
function hopLevels(
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

/** Hub center, rings by hop distance — writing-tool character map. */
function layoutHubConcentric(
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

function safeAddClass(ele: { empty?: () => boolean; addClass?: (c: string) => void } | null | undefined, cls: string) {
  if (!ele || typeof ele.addClass !== "function") return;
  if (typeof ele.empty === "function" && ele.empty()) return;
  ele.addClass(cls);
}

function applyNeighborhoodFocus(
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

export function RelationshipGraph(props: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const onSelectRef = useRef(props.onSelect);
  const selectedRef = useRef(props.selected);
  const [listOpen, setListOpen] = useState(true);
  const [ready, setReady] = useState(false);

  const slice = useMemo(
    () => displaySlice(props.nodes, props.edges),
    [props.nodes, props.edges]
  );
  const degree = useMemo(() => degreeMap(slice.edges), [slice.edges]);
  const hubId = useMemo(
    () => pickHubId(slice.nodes, degree),
    [slice.nodes, degree]
  );

  useEffect(() => {
    onSelectRef.current = props.onSelect;
  }, [props.onSelect]);
  useEffect(() => {
    selectedRef.current = props.selected;
  }, [props.selected]);

  const elements = useMemo(
    () => buildElements(slice.nodes, slice.edges, degree),
    [slice.nodes, slice.edges, degree]
  );

  const companionItems = useMemo(() => {
    const rankedNodes = [...slice.nodes].sort(
      (a, b) =>
        (degree.get(b.character_id) ?? 0) - (degree.get(a.character_id) ?? 0)
    );
    const nodeItems = rankedNodes.map((node) => ({
      key: `n${node.character_id}`,
      kind: "node" as const,
      characterId: node.character_id,
      observationId: undefined as number | undefined,
      label: node.name,
      meta:
        node.character_id === hubId
          ? `中心人物 · 连接 ${degree.get(node.character_id) ?? 0}`
          : `连接 ${degree.get(node.character_id) ?? 0} · 首见第 ${node.first_visible_chapter} 章`,
      isHub: node.character_id === hubId,
      provisional: false,
    }));
    const nameOf = (id: number) =>
      slice.nodes.find((n) => n.character_id === id)?.name ?? `#${id}`;
    const edgeItems = slice.edges.map((edge) => {
      const provisional = isProvisionalEdge(edge);
      return {
        key: edgeElementId(edge),
        kind: "edge" as const,
        characterId: undefined as number | undefined,
        observationId: edge.observation_id,
        label: `${nameOf(edge.source_character_id)} → ${nameOf(edge.target_character_id)}`,
        meta: edgeCompanionMeta(edge),
        isHub: false,
        provisional,
        transition: edge.transition,
      };
    });
    return [...nodeItems, ...edgeItems];
  }, [slice.nodes, slice.edges, degree, hubId]);

  // Mount / remount Cytoscape when visible set changes.
  useEffect(() => {
    let cancelled = false;
    let cy: Core | null = null;

    async function mount() {
      setReady(false);
      if (!containerRef.current) return;
      if (props.mode === "filters_required") {
        if (cyRef.current) {
          cyRef.current.destroy();
          cyRef.current = null;
        }
        props.onReady?.({
          zoomIn: () => undefined,
          zoomOut: () => undefined,
          fit: () => undefined,
          destroy: () => undefined,
        });
        return;
      }

      const cytoscape = (await import("cytoscape")).default;
      if (cancelled || !containerRef.current) return;

      if (cyRef.current) {
        cyRef.current.destroy();
        cyRef.current = null;
      }

      const preferLabels = slice.nodes.length <= 20;

      cy = cytoscape({
        container: containerRef.current,
        elements,
        pixelRatio: "auto" as unknown as number,
        // textureOnViewport paints a gray “outside” frame while panning — disable.
        textureOnViewport: false,
        hideEdgesOnViewport: false,
        motionBlur: false,
        boxSelectionEnabled: false,
        selectionType: "single",
        autoungrabify: false,
        style: [
          // Kill gray grab/selection chrome on the canvas itself.
          {
            selector: "core",
            style: {
              "active-bg-opacity": 0,
              "active-bg-size": 0,
              "selection-box-opacity": 0,
              "selection-box-border-width": 0,
              "outside-texture-bg-opacity": 0,
            },
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
              "text-max-width": 88,
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
              "line-style": "solid",
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
              "line-style": "solid",
            },
          })),
          // Provisional co-occurrence: dashed slate, label「共现」— visually honest.
          {
            selector: "edge.provisional, edge[relationType = \"cooccur\"]",
            style: {
              "line-color": PROVISIONAL_EDGE_COLOR,
              "target-arrow-color": PROVISIONAL_EDGE_COLOR,
              "line-style": "dashed",
              "line-dash-pattern": [6, 4],
              opacity: 0.75,
              color: "#64748b",
              label: preferLabels ? "共现" : "",
            },
          },
          {
            selector:
              "edge.provisional:selected, edge.provisional.highlighted, edge[relationType = \"cooccur\"]:selected",
            style: {
              "line-color": "#64748b",
              "target-arrow-color": "#64748b",
              "line-style": "dashed",
              opacity: 1,
              label: "共现",
            },
          },
        ],
        layout: { name: "null" },
        minZoom: 0.2,
        maxZoom: 3.5,
      });

      const levels = hopLevels(hubId, slice.nodes, slice.edges);
      try {
        cy.layout(layoutHubConcentric(levels, true)).run();
      } catch {
        cy.layout({ name: "grid", fit: true, animate: false } as LayoutOptions).run();
      }

      // Mark hub immediately for visual hierarchy.
      if (hubId != null) {
        safeAddClass(cy.getElementById(`n${hubId}`), "hub");
      }

      const onTap = (evt: EventObject) => {
        const t = evt.target;
        if (!t || t === cy) {
          onSelectRef.current(null);
          applyNeighborhoodFocus(cy!, null, null);
          if (hubId != null) safeAddClass(cy!.getElementById(`n${hubId}`), "hub");
          return;
        }
        if (t.isNode?.()) {
          const characterId = t.data("characterId") as number;
          onSelectRef.current({ kind: "node", characterId });
          applyNeighborhoodFocus(cy!, t.id(), null);
          return;
        }
        if (t.isEdge?.()) {
          onSelectRef.current({
            kind: "edge",
            observationId: t.data("observationId") as number,
          });
          applyNeighborhoodFocus(cy!, null, t.id());
        }
      };

      const onMouseOver = (evt: EventObject) => {
        const t = evt.target;
        if (!t?.isNode?.()) return;
        if (selectedRef.current) return; // selection owns focus
        applyNeighborhoodFocus(cy!, t.id(), null);
      };
      const onMouseOut = (evt: EventObject) => {
        if (selectedRef.current) return;
        if (!evt.target?.isNode?.()) return;
        applyNeighborhoodFocus(cy!, null, null);
        if (hubId != null) safeAddClass(cy!.getElementById(`n${hubId}`), "hub");
      };

      cy.on("tap", onTap);
      cy.on("mouseover", "node", onMouseOver);
      cy.on("mouseout", "node", onMouseOut);
      cyRef.current = cy;
      setReady(true);

      props.onReady?.({
        zoomIn: () => {
          const c = cyRef.current;
          if (!c) return;
          const level = Math.min(c.maxZoom(), c.zoom() * 1.35);
          c.zoom({
            level,
            renderedPosition: { x: c.width() / 2, y: c.height() / 2 },
          });
        },
        zoomOut: () => {
          const c = cyRef.current;
          if (!c) return;
          const level = Math.max(c.minZoom(), c.zoom() / 1.35);
          c.zoom({
            level,
            renderedPosition: { x: c.width() / 2, y: c.height() / 2 },
          });
        },
        fit: () => {
          cyRef.current?.fit(undefined, 40);
        },
        destroy: () => {
          if (cyRef.current) {
            cyRef.current.destroy();
            cyRef.current = null;
          }
        },
      });
    }

    void mount();

    return () => {
      cancelled = true;
      setReady(false);
      if (cy) {
        cy.removeAllListeners();
        cy.destroy();
      }
      if (cyRef.current) {
        cyRef.current.removeAllListeners();
        cyRef.current.destroy();
        cyRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements, hubId, props.mode, slice.nodes.length]);

  // Sync selection → neighborhood focus
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || props.mode === "filters_required") return;
    cy.elements().unselect();
    if (!props.selected) {
      applyNeighborhoodFocus(cy, null, null);
      if (hubId != null) safeAddClass(cy.getElementById(`n${hubId}`), "hub");
      return;
    }
    if (props.selected.kind === "node") {
      const id = `n${props.selected.characterId}`;
      const el = cy.getElementById(id);
      el?.select?.();
      applyNeighborhoodFocus(cy, id, null);
      return;
    }
    const edge = slice.edges.find(
      (e) => e.observation_id === props.selected!.observationId
    );
    if (edge) {
      const id = edgeElementId(edge);
      cy.getElementById(id)?.select?.();
      applyNeighborhoodFocus(cy, null, id);
    }
  }, [props.selected, props.mode, slice.edges, hubId]);

  if (props.mode === "filters_required") {
    return (
      <div
        data-testid="relationship-filters-required"
        className="grid min-h-64 place-items-center rounded-3xl border border-dashed p-6 text-center text-sm text-muted-foreground"
      >
        关系规模过大，未加载画布。请缩小筛选范围（人物 / 类型 / 章节）。
      </div>
    );
  }

  if (!props.nodes.length && !props.edges.length) {
    return (
      <div
        data-testid="relationship-empty"
        className="grid min-h-64 place-items-center rounded-3xl border border-dashed text-sm text-muted-foreground"
      >
        当前筛选没有可见人物关系。
      </div>
    );
  }

  const truncated =
    props.edges.length > slice.edges.length ||
    props.nodes.length > slice.nodes.length;
  const hubName =
    slice.nodes.find((n) => n.character_id === hubId)?.name ?? "核心人物";

  return (
    <section className="grid min-w-0 gap-3" aria-label="人物关系图">
      <div
        data-testid="relationship-canvas"
        className="min-w-0 overflow-hidden rounded-3xl border border-border/70 bg-[#fbfcf9]"
      >
        <div
          ref={containerRef}
          className={`h-[520px] w-full max-w-full touch-none bg-[#fbfcf9] outline-none transition-opacity duration-300 ease-out [&_canvas]:outline-none ${
            ready ? "opacity-100" : "opacity-40"
          }`}
          role="img"
          aria-label="人物关系网络图"
        />
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/50 px-3 py-2.5 text-xs text-muted-foreground">
          <p>
            以 <strong className="text-foreground">{hubName}</strong>{" "}
            为中心。实线边色：
            <span className="text-[#4f6f52]">同盟</span>/
            <span className="text-[#b45309]">敌对</span>/
            <span className="text-[#6366f1]">亲属</span>/
            <span className="text-[#0e7490]">师徒</span>/
            <span className="text-[#be185d]">爱慕</span>
            ；
            <span className="text-slate-500">灰色虚线=临时共现</span>
            。悬停聚焦邻接。
          </p>
          {truncated && (
            <p className="rounded-full bg-amber-50 px-2.5 py-1 text-amber-950">
              画布展示最强 {slice.edges.length} 条边 / {slice.nodes.length}{" "}
              人（共 {props.edges.length} 边）
            </p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          画布 {slice.nodes.length} 人 · {slice.edges.length} 条关系
          {props.edges.length !== slice.edges.length
            ? `（源数据 ${props.nodes.length}/${props.edges.length}）`
            : ""}
        </p>
        <button
          type="button"
          onClick={() => setListOpen((v) => !v)}
          className="rounded-lg border bg-card px-3 py-1.5 text-xs font-medium transition hover:border-primary"
        >
          {listOpen ? "收起人物列表" : "展开人物列表"}
        </button>
      </div>

      {listOpen && (
        <ul
          aria-label="人物关系键盘列表"
          data-testid="relationship-companion-list"
          className="grid max-h-[280px] gap-2 overflow-y-auto sm:grid-cols-2 xl:grid-cols-3"
        >
          {companionItems.map((item) => {
            const selected =
              item.kind === "node"
                ? props.selected?.kind === "node" &&
                  props.selected.characterId === item.characterId
                : props.selected?.kind === "edge" &&
                  props.selected.observationId === item.observationId;
            return (
              <li key={item.key}>
                <button
                  type="button"
                  onClick={() => {
                    if (item.kind === "node" && item.characterId != null) {
                      props.onSelect({
                        kind: "node",
                        characterId: item.characterId,
                      });
                    } else if (
                      item.kind === "edge" &&
                      item.observationId != null
                    ) {
                      props.onSelect({
                        kind: "edge",
                        observationId: item.observationId,
                      });
                    }
                  }}
                  className={`h-full w-full rounded-2xl border p-3 text-left transition-[border-color,box-shadow,background-color,transform] duration-200 ease-out hover:-translate-y-0.5 hover:border-primary hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                    selected
                      ? "border-primary bg-primary/5"
                      : item.isHub
                        ? "border-amber-300/80 bg-amber-50/40"
                        : "bg-card"
                  }`}
                >
                  <span className="text-xs text-muted-foreground">
                    {item.kind === "edge"
                      ? item.provisional
                        ? "共现"
                        : "关系"
                      : item.isHub
                        ? "中心"
                        : "人物"}{" "}
                    · {item.meta}
                  </span>
                  {item.kind === "edge" &&
                    !item.provisional &&
                    item.transition &&
                    item.transition !== "establish" && (
                      <span
                        data-testid="relationship-transition-badge"
                        data-transition={item.transition}
                        className="mt-1 inline-flex rounded-full border border-amber-300/80 bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-950"
                      >
                        {item.transition === "change" ? "关系变化" : "关系结束"}
                      </span>
                    )}
                  <p className="mt-1 line-clamp-1 font-serif text-sm font-semibold">
                    {item.label}
                  </p>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
