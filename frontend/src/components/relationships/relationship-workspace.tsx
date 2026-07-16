"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  relationshipsApi,
  type RelationshipEdgeType,
  type RelationshipEvidenceResponse,
  type RelationshipGraphEdge,
  type RelationshipGraphEnvelope,
  type RelationshipVersionSource,
} from "@/lib/api";
import { RelationshipControls } from "./relationship-controls";
import { RelationshipEvidencePanel } from "./relationship-evidence-panel";
import {
  RelationshipGraph,
  type GraphSelection,
} from "./relationship-graph";

type Props = {
  novelId: string;
  /** Shared analysis version source with timeline (active / running_candidate). */
  source: "active" | "running_candidate";
  versionId?: number | null;
  fullBook: boolean;
  /** Shared narrative position from timeline selection (chapter number). */
  throughChapter: number | "";
  onThroughChapterChange: (value: number | "") => void;
  maxChapter?: number;
};

export function RelationshipWorkspace(props: Props) {
  const [envelope, setEnvelope] = useState<RelationshipGraphEnvelope | null>(
    null
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [characterId, setCharacterId] = useState<number | "">("");
  const [relationType, setRelationType] = useState<RelationshipEdgeType | "">(
    ""
  );
  const [selected, setSelected] = useState<GraphSelection>(null);
  const [evidence, setEvidence] = useState<RelationshipEvidenceResponse | null>(
    null
  );
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState("");
  const zoomApiRef = useRef<{
    zoomIn: () => void;
    zoomOut: () => void;
    fit: () => void;
    destroy: () => void;
  } | null>(null);
  const requestIdRef = useRef(0);
  const evidenceRequestIdRef = useRef(0);

  const loadGraph = useCallback(async () => {
    if (!props.novelId) return;
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError("");
    try {
      const source: RelationshipVersionSource = props.source;
      const response = await relationshipsApi.getGraph(props.novelId, {
        source,
        version_id: props.versionId ?? undefined,
        through_chapter:
          props.throughChapter === "" ? undefined : props.throughChapter,
        full_book: props.fullBook,
        character_id: characterId === "" ? undefined : characterId,
        relation_type: relationType === "" ? undefined : relationType,
      });
      if (requestId !== requestIdRef.current) return;
      setEnvelope(response.data);
      setSelected((prev) => {
        if (!prev) return null;
        if (prev.kind === "node") {
          return response.data.nodes.some(
            (n) => n.character_id === prev.characterId
          )
            ? prev
            : null;
        }
        return response.data.edges.some(
          (e) => e.observation_id === prev.observationId
        )
          ? prev
          : null;
      });
    } catch {
      if (requestId !== requestIdRef.current) return;
      setEnvelope(null);
      setError("加载人物关系失败。");
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }, [
    props.novelId,
    props.source,
    props.versionId,
    props.throughChapter,
    props.fullBook,
    characterId,
    relationType,
  ]);

  useEffect(() => {
    let cancelled = false;
    // Defer so the first setState is not synchronous inside the effect body.
    queueMicrotask(() => {
      if (!cancelled) void loadGraph();
    });
    return () => {
      cancelled = true;
    };
  }, [loadGraph]);

  useEffect(() => {
    return () => {
      zoomApiRef.current?.destroy();
      zoomApiRef.current = null;
    };
  }, []);

  const selectedEdge: RelationshipGraphEdge | null = useMemo(() => {
    if (!envelope || selected?.kind !== "edge") return null;
    return (
      envelope.edges.find((e) => e.observation_id === selected.observationId) ??
      null
    );
  }, [envelope, selected]);

  useEffect(() => {
    let cancelled = false;
    const requestId = ++evidenceRequestIdRef.current;
    const edge = selectedEdge;
    const novelId = props.novelId;

    queueMicrotask(() => {
      if (cancelled || requestId !== evidenceRequestIdRef.current) return;
      if (!edge || !novelId) {
        setEvidence(null);
        setEvidenceLoading(false);
        setEvidenceError("");
        return;
      }
      setEvidenceLoading(true);
      setEvidenceError("");
      relationshipsApi
        .getEvidence(novelId, edge.observation_id, {
          source: props.source,
          version_id: props.versionId ?? undefined,
          through_chapter:
            props.throughChapter === "" ? undefined : props.throughChapter,
          full_book: props.fullBook,
        })
        .then((res) => {
          if (cancelled || requestId !== evidenceRequestIdRef.current) return;
          setEvidence(res.data);
        })
        .catch(() => {
          if (cancelled || requestId !== evidenceRequestIdRef.current) return;
          setEvidence(null);
          setEvidenceError("证据不可见或不存在。");
        })
        .finally(() => {
          if (cancelled || requestId !== evidenceRequestIdRef.current) return;
          setEvidenceLoading(false);
        });
    });

    return () => {
      cancelled = true;
    };
  }, [
    selectedEdge,
    props.novelId,
    props.source,
    props.versionId,
    props.throughChapter,
    props.fullBook,
  ]);

  const nodesById = useMemo(() => {
    const map = new Map(
      (envelope?.nodes ?? []).map((n) => [n.character_id, n])
    );
    return map;
  }, [envelope]);

  const filterNodes = envelope?.nodes?.length
    ? envelope.nodes
    : (envelope?.available_character_ids ?? []).map((id) => ({
        character_id: id,
        name: `人物 #${id}`,
        aliases: [] as string[],
        first_visible_chapter: 1,
      }));

  const availableTypes = envelope?.available_relation_types?.length
    ? envelope.available_relation_types
    : ([
        "ally",
        "enemy",
        "family",
        "mentor",
        "romantic",
      ] as RelationshipEdgeType[]);

  const mode = envelope?.degradation?.mode ?? "normal";

  return (
    <div className="grid min-w-0 gap-3" data-testid="relationship-workspace">
      <RelationshipControls
        nodes={filterNodes}
        availableRelationTypes={availableTypes}
        characterId={characterId}
        relationType={relationType}
        throughChapter={props.throughChapter}
        maxChapter={props.maxChapter}
        onCharacterChange={setCharacterId}
        onRelationTypeChange={setRelationType}
        onThroughChapterChange={props.onThroughChapterChange}
        onZoomIn={() => zoomApiRef.current?.zoomIn()}
        onZoomOut={() => zoomApiRef.current?.zoomOut()}
        onFit={() => zoomApiRef.current?.fit()}
        degradationMode={mode}
      />

      {envelope && (
        <p className="text-xs text-muted-foreground">
          版本 v{envelope.version_id} · 截止第 {envelope.cutoff_chapter} 章 ·
          可见 {envelope.counts.nodes} 人 / {envelope.counts.edges} 边
          {envelope.full_book ? " · 全书" : ""}
          {mode !== "normal" ? ` · 模式 ${mode}` : ""}
        </p>
      )}

      {error && (
        <p
          role="alert"
          className="rounded-xl bg-destructive/10 p-3 text-sm text-destructive"
        >
          {error}
        </p>
      )}

      {loading && !envelope ? (
        <div
          className="grid h-96 min-h-96 place-items-center rounded-3xl bg-muted motion-transition-content"
          role="status"
          aria-busy="true"
          aria-label="正在加载人物关系"
        >
          <p className="text-sm text-muted-foreground">正在加载人物关系…</p>
        </div>
      ) : envelope ? (
        <RelationshipGraph
          nodes={envelope.nodes}
          edges={envelope.edges}
          mode={mode}
          selected={selected}
          onSelect={setSelected}
          onReady={(api) => {
            zoomApiRef.current = api;
          }}
        />
      ) : (
        !loading && (
          <div className="grid min-h-64 place-items-center rounded-3xl border border-dashed p-8 text-center text-muted-foreground">
            <p className="text-sm">暂无人物关系数据。</p>
            <p className="mt-2 max-w-md text-xs leading-5">
              关系观察在<strong>时间线完成并发布</strong>
              后自动抽取。当前若时间线仅有「候选结果」或任务已暂停，请先回到时间线点
              「继续分析」跑完；完成后会自动入队关系任务。
            </p>
          </div>
        )
      )}
      {envelope &&
        !loading &&
        envelope.counts.nodes === 0 &&
        envelope.counts.edges === 0 && (
          <p className="rounded-xl border border-amber-300/70 bg-amber-50 px-3 py-2 text-xs text-amber-950">
            已绑定版本 v{envelope.version_id}，但尚无已接受的关系观察（0 人 / 0
            边）。通常表示关系抽取尚未成功完成。
          </p>
        )}

      <RelationshipEvidencePanel
        novelId={props.novelId}
        edge={selectedEdge}
        nodesById={nodesById}
        evidence={evidence}
        loading={evidenceLoading}
        error={evidenceError}
        onClose={() => {
          setSelected(null);
          setEvidence(null);
        }}
      />
    </div>
  );
}
