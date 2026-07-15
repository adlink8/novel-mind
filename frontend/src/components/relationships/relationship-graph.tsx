"use client";

/**
 * Cytoscape.js relationship graph (D-19).
 * Client-only instance lifecycle; canvas + keyboard list share the same arrays.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  Core,
  ElementDefinition,
  EventObject,
  LayoutOptions,
} from "cytoscape";

import type {
  GraphDegradationMode,
  RelationshipGraphEdge,
  RelationshipGraphNode,
} from "@/lib/api";
import { RELATION_LABELS } from "./relationship-controls";

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
  /** Exposed for parent zoom controls */
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

function buildElements(
  nodes: RelationshipGraphNode[],
  edges: RelationshipGraphEdge[]
): ElementDefinition[] {
  const nodeEls: ElementDefinition[] = nodes.map((node) => ({
    group: "nodes",
    data: {
      id: `n${node.character_id}`,
      characterId: node.character_id,
      label: node.name,
    },
  }));
  const edgeEls: ElementDefinition[] = edges.map((edge) => ({
    group: "edges",
    data: {
      id: `e${edge.observation_id}`,
      observationId: edge.observation_id,
      source: `n${edge.source_character_id}`,
      target: `n${edge.target_character_id}`,
      relationType: edge.relation_type,
      label: RELATION_LABELS[edge.relation_type] ?? edge.relation_type,
    },
  }));
  return [...nodeEls, ...edgeEls];
}

function layoutForMode(mode: GraphDegradationMode): LayoutOptions {
  if (mode === "large") {
    return {
      name: "concentric",
      animate: false,
      fit: true,
      padding: 24,
      minNodeSpacing: 18,
      concentric: () => 1,
      levelWidth: () => 1,
    } as LayoutOptions;
  }
  return {
    name: "cose",
    animate: false,
    fit: true,
    padding: 32,
    nodeRepulsion: () => 4500,
    idealEdgeLength: () => 80,
    numIter: 400,
  } as LayoutOptions;
}

export function RelationshipGraph(props: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const onSelectRef = useRef(props.onSelect);
  const [listOpen, setListOpen] = useState(true);

  useEffect(() => {
    onSelectRef.current = props.onSelect;
  }, [props.onSelect]);

  const elements = useMemo(
    () => buildElements(props.nodes, props.edges),
    [props.nodes, props.edges]
  );

  const companionItems = useMemo(() => {
    const nodeItems = props.nodes.map((node) => ({
      key: `n${node.character_id}`,
      kind: "node" as const,
      characterId: node.character_id,
      label: node.name,
      meta: `首见第 ${node.first_visible_chapter} 章`,
    }));
    const edgeItems = props.edges.map((edge) => {
      const source =
        props.nodes.find((n) => n.character_id === edge.source_character_id)
          ?.name ?? `#${edge.source_character_id}`;
      const target =
        props.nodes.find((n) => n.character_id === edge.target_character_id)
          ?.name ?? `#${edge.target_character_id}`;
      return {
        key: `e${edge.observation_id}`,
        kind: "edge" as const,
        observationId: edge.observation_id,
        label: `${source} → ${target}`,
        meta: RELATION_LABELS[edge.relation_type] ?? edge.relation_type,
      };
    });
    return [...nodeItems, ...edgeItems];
  }, [props.nodes, props.edges]);

  // Create / recycle Cytoscape instance when visible set or mode changes.
  useEffect(() => {
    let cancelled = false;
    let cy: Core | null = null;

    async function mount() {
      if (!containerRef.current) return;
      // filters_required: never instantiate with partial elements
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

      const isLarge = props.mode === "large";
      cy = cytoscape({
        container: containerRef.current,
        elements,
        pixelRatio: isLarge ? 1 : ("auto" as unknown as number),
        style: [
          {
            selector: "node",
            style: {
              "background-color": "#4f6f52",
              label: isLarge ? "" : "data(label)",
              color: "#1c1917",
              "font-size": 11,
              "text-valign": "bottom",
              "text-margin-y": 6,
              width: isLarge ? 18 : 28,
              height: isLarge ? 18 : 28,
              "border-width": 1,
              "border-color": "#d6d3d1",
            },
          },
          {
            selector: "node:selected",
            style: {
              "background-color": "#1c1917",
              label: "data(label)",
              "border-color": "#f59e0b",
              "border-width": 2,
            },
          },
          {
            selector: "edge",
            style: {
              width: isLarge ? 1 : 2,
              "line-color": "#a8a29e",
              "target-arrow-color": "#a8a29e",
              "target-arrow-shape": isLarge ? "none" : "triangle",
              "curve-style": isLarge ? "haystack" : "bezier",
              label: isLarge ? "" : "data(label)",
              "font-size": 9,
              color: "#57534e",
              "text-rotation": "autorotate",
            },
          },
          {
            selector: "edge:selected",
            style: {
              width: 3,
              "line-color": "#b45309",
              "target-arrow-color": "#b45309",
              label: "data(label)",
            },
          },
          ...Object.entries(EDGE_COLORS).map(([type, color]) => ({
            selector: `edge[relationType = "${type}"]`,
            style: {
              "line-color": color,
              "target-arrow-color": color,
            },
          })),
        ],
        layout: layoutForMode(props.mode),
        minZoom: 0.2,
        maxZoom: 3,
        wheelSensitivity: 0.25,
      });

      const onTap = (evt: EventObject) => {
        const t = evt.target;
        if (!t || t === cy) {
          onSelectRef.current(null);
          return;
        }
        if (t.isNode?.()) {
          onSelectRef.current({
            kind: "node",
            characterId: t.data("characterId") as number,
          });
          return;
        }
        if (t.isEdge?.()) {
          onSelectRef.current({
            kind: "edge",
            observationId: t.data("observationId") as number,
          });
        }
      };

      cy.on("tap", onTap);
      cyRef.current = cy;

      props.onReady?.({
        zoomIn: () => {
          const c = cyRef.current;
          if (!c) return;
          c.zoom({
            level: c.zoom() * 1.2,
            renderedPosition: {
              x: c.width() / 2,
              y: c.height() / 2,
            },
          });
        },
        zoomOut: () => {
          const c = cyRef.current;
          if (!c) return;
          c.zoom({
            level: c.zoom() / 1.2,
            renderedPosition: {
              x: c.width() / 2,
              y: c.height() / 2,
            },
          });
        },
        fit: () => {
          cyRef.current?.fit(undefined, 32);
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
    // elements identity via nodes/edges arrays + mode
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.nodes, props.edges, props.mode]);

  // Sync selection highlight
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || props.mode === "filters_required") return;
    cy.elements().unselect();
    if (!props.selected) return;
    if (props.selected.kind === "node") {
      cy.getElementById(`n${props.selected.characterId}`).select();
    } else {
      cy.getElementById(`e${props.selected.observationId}`).select();
    }
  }, [props.selected, props.mode, props.nodes, props.edges]);

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

  return (
    <section className="grid min-w-0 gap-3" aria-label="人物关系图">
      <div
        data-testid="relationship-canvas"
        className="min-w-0 overflow-hidden rounded-3xl border bg-card"
      >
        <div
          ref={containerRef}
          className="h-[420px] w-full max-w-full"
          role="img"
          aria-label="人物关系网络图"
        />
        <p className="px-3 pb-2 text-xs text-muted-foreground">
          滚轮缩放、拖动画布。点边查看证据；下方列表与画布同源。
        </p>
      </div>

      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          共 {props.nodes.length} 人 · {props.edges.length} 条关系
        </p>
        <button
          type="button"
          onClick={() => setListOpen((v) => !v)}
          className="rounded-lg border bg-card px-3 py-1.5 text-xs font-medium"
        >
          {listOpen ? "收起列表" : "展开同源列表"}
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
                    if (item.kind === "node") {
                      props.onSelect({
                        kind: "node",
                        characterId: item.characterId,
                      });
                    } else {
                      props.onSelect({
                        kind: "edge",
                        observationId: item.observationId,
                      });
                    }
                  }}
                  className={`h-full w-full rounded-2xl border p-3 text-left transition hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                    selected ? "border-primary bg-primary/5" : "bg-card"
                  }`}
                >
                  <span className="text-xs text-muted-foreground">
                    {item.kind === "node" ? "人物" : "关系"} · {item.meta}
                  </span>
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
