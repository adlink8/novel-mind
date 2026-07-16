"use client";

import type {
  RelationshipEdgeType,
  RelationshipGraphNode,
} from "@/lib/api";

const RELATION_LABELS: Record<RelationshipEdgeType, string> = {
  ally: "同盟",
  enemy: "敌对",
  family: "亲属",
  mentor: "师徒",
  romantic: "爱慕",
};

type Props = {
  nodes: RelationshipGraphNode[];
  availableRelationTypes: RelationshipEdgeType[];
  characterId: number | "";
  relationType: RelationshipEdgeType | "";
  throughChapter: number | "";
  maxChapter?: number;
  onCharacterChange: (value: number | "") => void;
  onRelationTypeChange: (value: RelationshipEdgeType | "") => void;
  onThroughChapterChange: (value: number | "") => void;
  onZoomIn?: () => void;
  onZoomOut?: () => void;
  onFit?: () => void;
  degradationMode?: string;
};

export function RelationshipControls(props: Props) {
  return (
    <section
      aria-label="人物关系控制"
      className="flex flex-wrap items-end gap-3 rounded-2xl border bg-card/80 p-3"
    >
      <label className="grid min-w-40 gap-1 text-xs text-muted-foreground">
        筛选人物
        <select
          aria-label="筛选人物"
          value={props.characterId === "" ? "" : String(props.characterId)}
          onChange={(event) => {
            const raw = event.target.value;
            props.onCharacterChange(raw === "" ? "" : Number(raw));
          }}
          className="h-10 rounded-xl border bg-background px-3 text-sm text-foreground"
        >
          <option value="">全部人物</option>
          {props.nodes.map((node) => (
            <option key={node.character_id} value={node.character_id}>
              {node.name}
            </option>
          ))}
        </select>
      </label>

      <label className="grid min-w-36 gap-1 text-xs text-muted-foreground">
        关系类型
        <select
          aria-label="筛选关系类型"
          value={props.relationType}
          onChange={(event) =>
            props.onRelationTypeChange(
              (event.target.value || "") as RelationshipEdgeType | ""
            )
          }
          className="h-10 rounded-xl border bg-background px-3 text-sm text-foreground"
        >
          <option value="">全部类型</option>
          {props.availableRelationTypes.map((type) => (
            <option key={type} value={type}>
              {RELATION_LABELS[type] ?? type}
            </option>
          ))}
        </select>
      </label>

      <label className="grid min-w-28 gap-1 text-xs text-muted-foreground">
        叙事位置（章）
        <input
          type="number"
          min={1}
          max={props.maxChapter}
          aria-label="叙事位置章节"
          value={props.throughChapter === "" ? "" : props.throughChapter}
          onChange={(event) => {
            const raw = event.target.value;
            if (raw === "") {
              props.onThroughChapterChange("");
              return;
            }
            const n = Number(raw);
            if (Number.isFinite(n) && n > 0) {
              props.onThroughChapterChange(n);
            }
          }}
          placeholder="跟随默认"
          className="h-10 w-28 rounded-xl border bg-background px-3 text-sm text-foreground"
        />
      </label>

      <div
        className="flex flex-wrap gap-2"
        role="group"
        aria-label="关系图缩放"
      >
        <button
          type="button"
          onClick={props.onZoomIn}
          className="h-10 rounded-xl border bg-background px-3 text-sm"
        >
          放大
        </button>
        <button
          type="button"
          onClick={props.onZoomOut}
          className="h-10 rounded-xl border bg-background px-3 text-sm"
        >
          缩小
        </button>
        <button
          type="button"
          onClick={props.onFit}
          className="h-10 rounded-xl border bg-background px-3 text-sm"
        >
          适配
        </button>
      </div>

      {props.degradationMode === "large" && (
        <p className="w-full text-xs text-amber-800">
          大图模式：画布以连接度最高人物为中心分层，悬停可聚焦邻接。
        </p>
      )}
      {props.degradationMode === "filters_required" && (
        <p className="w-full text-xs text-destructive" role="status">
          可见关系超过上限，请先用人物/类型/章节筛选后再渲染图。
        </p>
      )}
    </section>
  );
}

export { RELATION_LABELS };
