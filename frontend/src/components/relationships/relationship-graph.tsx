"use client";

/**
 * Cytoscape relationship graph.
 * Layout inspired by writing tools (Milanote / character maps):
 * hub character center → secondary ring → supporting outer; labeled links;
 * layout animation + neighborhood focus on select/hover.
 *
 * Pure math lives in graph-layout.ts, cytoscape builders in
 * graph-cytoscape.ts, companion-list items in graph-companion.ts.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  Core,
  EventObject,
  LayoutOptions,
} from "cytoscape";

import type {
  GraphDegradationMode,
  RelationshipGraphEdge,
  RelationshipGraphNode,
} from "@/lib/api";
import { buildCompanionItems } from "./graph-companion";
import {
  applyNeighborhoodFocus,
  buildElements,
  buildStylesheet,
  layoutHubConcentric,
  safeAddClass,
} from "./graph-cytoscape";
import {
  degreeMap,
  displaySlice,
  edgeElementId,
  hopLevels,
  pickHubId,
} from "./graph-layout";

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

// Re-export for existing tests / consumers.
export { isProvisionalEdge } from "./relationship-honesty";

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

  const companionItems = useMemo(
    () => buildCompanionItems(slice.nodes, slice.edges, degree, hubId),
    [slice.nodes, slice.edges, degree, hubId]
  );

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
        style: buildStylesheet(preferLabels),
        layout: { name: "null" },
        minZoom: 0.2,
        maxZoom: 3.5,
      });

      const levels = hopLevels(hubId, slice.nodes, slice.edges);
      const reduceMotion =
        typeof window !== "undefined" &&
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      try {
        cy.layout(layoutHubConcentric(levels, !reduceMotion)).run();
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
    const selected = props.selected;
    if (!selected) {
      applyNeighborhoodFocus(cy, null, null);
      if (hubId != null) safeAddClass(cy.getElementById(`n${hubId}`), "hub");
      return;
    }
    if (selected.kind === "node") {
      const id = `n${selected.characterId}`;
      const el = cy.getElementById(id);
      el?.select?.();
      applyNeighborhoodFocus(cy, id, null);
      return;
    }
    const edge = slice.edges.find(
      (e) => e.observation_id === selected.observationId
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
