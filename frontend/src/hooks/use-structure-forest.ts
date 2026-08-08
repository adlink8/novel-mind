"use client";

/**
 * 结构树 / 叙事记忆（NM）数据流 hook。
 *
 * 从「结构为脊梁」的 Phase 20 分析页拆分而来：负责结构树加载
 * （章节回退树 / NM 树）、NM claims 与证据链接的两个 effect，
 * 以及结构节点选择对 facet 的联动。timeline 数据流见
 * use-timeline-workspace.ts；两者共享的 selectedNodeRef 由本 hook 持有，
 * timeline hook 通过参数读取其 chapterStart/chapterEnd 作为服务端范围。
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import type { AnalysisChapterRef } from "@/components/analysis/analysis-chat-panel";
import {
  buildChapterFallbackTree,
  buildNmStructureTree,
  findTreeNodeById,
  findTreeNodeByNmId,
  pickDefaultTreeNode,
  treeNodeToSelection,
} from "@/components/structure/build-structure-tree";
import type {
  StructureNodeSelection,
  StructureSource,
  StructureTreeNode,
} from "@/components/structure/structure-types";
import type { Novel } from "@/lib/api";
import { novelsApi } from "@/lib/api";
import {
  narrativeMemoryApi,
  pickLatestPreviewVersion,
  type NmClaimItem,
  type NmSourceLinkItem,
} from "@/lib/narrative-memory-api";

export function useStructureForest(params: {
  novelId: string;
  /** 用户显式设置的剧透上限（关系/时间线点击）。 */
  throughChapter: number | "";
  /** 结构树只响应显式上限；节点选择对 throughChapter 的自动对齐是 facet 过滤用途。 */
  explicitThrough: number | "";
  /** selectedNovel?.chapter_count ?? 1 —— 由页面派生后传入，避免 hooks 间循环依赖。 */
  chapterCount: number;
  setChapterList: Dispatch<SetStateAction<AnalysisChapterRef[]>>;
}) {
  const {
    novelId,
    throughChapter,
    explicitThrough,
    chapterCount,
    setChapterList,
  } = params;

  const [structureSource, setStructureSource] =
    useState<StructureSource>("chapters");
  const [structureForest, setStructureForest] = useState<StructureTreeNode[]>(
    []
  );
  const [selectedNode, setSelectedNode] =
    useState<StructureNodeSelection | null>(null);
  const [nmVersionId, setNmVersionId] = useState<number | null>(null);
  const [nmClaims, setNmClaims] = useState<NmClaimItem[]>([]);
  const [nmClaimsLoading, setNmClaimsLoading] = useState(false);
  const [nmClaimsError, setNmClaimsError] = useState<string | null>(null);
  const [selectedClaimId, setSelectedClaimId] = useState<number | null>(null);
  const [nmSourceLinks, setNmSourceLinks] = useState<NmSourceLinkItem[]>([]);
  const [nmSourceLinksLoading, setNmSourceLinksLoading] = useState(false);
  const [nmSourceLinksError, setNmSourceLinksError] = useState<string | null>(
    null
  );

  /** Last through_chapter used for NM tree fetch (re-fetch on spoiler change). */
  const nmTreeThroughRef = useRef<number | null>(null);
  /** 共享：timeline hook 的 loadTimeline 读取它做服务端章节范围。 */
  const selectedNodeRef = useRef<StructureNodeSelection | null>(null);
  /** 选中作品的章节真实标题（chapters 表），结构树标签统一用它补全章节名。 */
  const chapterTitlesRef = useRef<Record<number, string>>({});
  // Shared with the timeline hook across renders; updated synchronously so the
  // poll tick / loadTimeline always sees the latest structure scope.
  // eslint-disable-next-line react-hooks/refs -- render-sync mirrors state into the escaping shared ref
  selectedNodeRef.current = selectedNode;

  const applyChapterForest = useCallback((chapterCountValue: number) => {
    const forest = buildChapterFallbackTree(
      Math.max(1, chapterCountValue || 1),
      {
        titles: chapterTitlesRef.current,
      }
    );
    setStructureSource("chapters");
    setStructureForest(forest);
    setNmVersionId(null);
    setNmClaims([]);
    setNmClaimsError(null);
    setSelectedClaimId(null);
    setNmSourceLinks([]);
    setNmSourceLinksError(null);
    nmTreeThroughRef.current = null;
    const def = pickDefaultTreeNode(forest);
    const selection = def ? treeNodeToSelection(def) : null;
    selectedNodeRef.current = selection;
    setSelectedNode(selection);
  }, []);

  const applyNmForest = useCallback(
    (
      forest: StructureTreeNode[],
      versionId: number,
      through: number,
      opts?: { preserveSelection?: boolean }
    ) => {
      setStructureSource("narrative_memory");
      setStructureForest(forest);
      setNmVersionId(versionId);
      nmTreeThroughRef.current = through;
      setNmClaims([]);
      setNmClaimsError(null);
      setSelectedClaimId(null);
      setNmSourceLinks([]);
      setNmSourceLinksError(null);

      const applySelection = (node: StructureTreeNode | null) => {
        const selection = node ? treeNodeToSelection(node) : null;
        selectedNodeRef.current = selection;
        setSelectedNode(selection);
      };

      if (opts?.preserveSelection) {
        const prev = selectedNodeRef.current;
        if (prev?.nmNodeId != null) {
          const found = findTreeNodeByNmId(forest, prev.nmNodeId);
          if (found) {
            applySelection(found);
            return;
          }
        }
        if (prev?.id) {
          const found = findTreeNodeById(forest, prev.id);
          if (found) {
            applySelection(found);
            return;
          }
        }
      }
      applySelection(pickDefaultTreeNode(forest));
    },
    []
  );

  const loadStructure = useCallback(
    async (
      id: string,
      novelMeta: Novel | undefined,
      /** When selecting a novel, pass "" so we do not reuse prior book through_chapter. */
      throughOverride?: number | "",
      opts?: { preserveSelection?: boolean }
    ) => {
      const chapterCountValue = novelMeta?.chapter_count ?? 1;
      try {
        // 章节标题与 NM 版本并行取；标题只用于树标签，失败不阻断主流程
        const [versionsRes, chaptersSettled] = await Promise.all([
          narrativeMemoryApi.listVersions(id),
          Promise.allSettled([novelsApi.getChapters(id)]),
        ]);
        const [chaptersOutcome] = chaptersSettled;
        if (chaptersOutcome.status === "fulfilled") {
          const map: Record<number, string> = {};
          for (const ch of chaptersOutcome.value.data ?? []) {
            if (typeof ch.chapter_number === "number" && ch.title) {
              map[ch.chapter_number] = ch.title;
            }
          }
          chapterTitlesRef.current = map;
          // 对话面板复用同一份章节数据（id ↔ 章号映射）
          setChapterList(
            (chaptersOutcome.value.data ?? []).map((ch) => ({
              id: ch.id,
              chapter_number: ch.chapter_number,
              title: ch.title,
            }))
          );
        }
        const latest = pickLatestPreviewVersion(
          versionsRes.data.versions ?? []
        );
        if (!latest) {
          applyChapterForest(chapterCountValue);
          return;
        }
        const effectiveThrough =
          throughOverride !== undefined ? throughOverride : throughChapter;
        const through =
          typeof effectiveThrough === "number" && effectiveThrough >= 1
            ? effectiveThrough
            : chapterCountValue || undefined;
        const treeRes = await narrativeMemoryApi.getTree(
          id,
          latest.version_id,
          {
            through_chapter: through,
          }
        );
        const forest = buildNmStructureTree(treeRes.data.nodes ?? [], {
          chapterTitles: chapterTitlesRef.current,
        });
        if (!forest.length) {
          applyChapterForest(chapterCountValue);
          return;
        }
        const resolvedThrough =
          typeof through === "number" && through >= 1
            ? through
            : chapterCountValue;
        applyNmForest(forest, latest.version_id, resolvedThrough, {
          preserveSelection: opts?.preserveSelection,
        });
      } catch {
        // Honest fallback: no NM or API error → chapter structure only
        applyChapterForest(chapterCountValue);
      }
    },
    [applyChapterForest, applyNmForest, throughChapter, setChapterList]
  );

  /**
   * selectNovel 里的结构侧重置（timeline hook 的 selectNovel 调用）。
   * 选书即清理上一本书的 NM 树/claims；空 id 时整树回退为空。
   */
  const prepareForNovel = useCallback(
    (id: string, novelMeta: Novel | undefined) => {
      setNmClaims([]);
      setNmClaimsError(null);
      setSelectedClaimId(null);
      setNmSourceLinks([]);
      setNmSourceLinksError(null);
      setNmVersionId(null);
      nmTreeThroughRef.current = null;
      setChapterList([]);
      if (!id) {
        setStructureForest([]);
        chapterTitlesRef.current = {};
        selectedNodeRef.current = null;
        setSelectedNode(null);
        setStructureSource("chapters");
        return;
      }
      applyChapterForest(novelMeta?.chapter_count ?? 1);
    },
    [applyChapterForest, setChapterList]
  );

  /** 结构节点选中：写 selectedNode + 共享 ref，并清空上一条 claim 证据链。 */
  const selectStructureNode = useCallback((node: StructureTreeNode) => {
    const selection = treeNodeToSelection(node);
    setSelectedNode(selection);
    selectedNodeRef.current = selection;
    setSelectedClaimId(null);
    setNmSourceLinks([]);
    setNmSourceLinksError(null);
  }, []);

  function handleClaimSelect(claim: NmClaimItem) {
    setSelectedClaimId((prev) => (prev === claim.id ? null : claim.id));
  }

  /**
   * Relationships: fold at min(user through, node.chapterEnd) when structure
   * selection is active. Server remains spoiler authority for through_chapter.
   */
  const relationshipThroughChapter = useMemo((): number | "" => {
    if (!selectedNode) return throughChapter;
    const nodeEnd = selectedNode.chapterEnd;
    if (throughChapter === "" || throughChapter == null) return nodeEnd;
    return Math.min(Number(throughChapter), nodeEnd);
  }, [selectedNode, throughChapter]);

  // Re-fetch NM tree only when the user sets an EXPLICIT spoiler cap
  // (mid-session). Auto-alignment from node selection must not shrink the tree.
  useEffect(() => {
    if (!novelId || structureSource !== "narrative_memory" || !nmVersionId) {
      return;
    }
    const through =
      typeof explicitThrough === "number" && explicitThrough >= 1
        ? explicitThrough
        : chapterCount;
    if (nmTreeThroughRef.current === through) return;
    let cancelled = false;
    (async () => {
      try {
        const treeRes = await narrativeMemoryApi.getTree(novelId, nmVersionId, {
          through_chapter: through,
        });
        if (cancelled) return;
        const forest = buildNmStructureTree(treeRes.data.nodes ?? [], {
          chapterTitles: chapterTitlesRef.current,
        });
        if (!forest.length) {
          applyChapterForest(chapterCount);
          return;
        }
        applyNmForest(forest, nmVersionId, through, { preserveSelection: true });
      } catch {
        // Keep previous tree on transient failure
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    novelId,
    structureSource,
    nmVersionId,
    explicitThrough,
    chapterCount,
    applyChapterForest,
    applyNmForest,
  ]);

  // Load NM claims for selected NM node (read-only candidate preview)
  useEffect(() => {
    if (
      !novelId ||
      structureSource !== "narrative_memory" ||
      !nmVersionId ||
      !selectedNode?.nmNodeId
    ) {
      // 依赖切换时的同步重置；正规做法是 render 期派生或 key 重挂载，Phase 25 再收
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setNmClaims([]);
      setNmClaimsError(null);
      setNmClaimsLoading(false);
      setSelectedClaimId(null);
      setNmSourceLinks([]);
      return;
    }
    let cancelled = false;
    setNmClaimsLoading(true);
    setNmClaimsError(null);
    setSelectedClaimId(null);
    setNmSourceLinks([]);
    setNmSourceLinksError(null);
    const through =
      typeof relationshipThroughChapter === "number"
        ? relationshipThroughChapter
        : selectedNode.chapterEnd;
    narrativeMemoryApi
      .getClaims(novelId, nmVersionId, selectedNode.nmNodeId, {
        through_chapter: through,
      })
      .then((res) => {
        if (cancelled) return;
        setNmClaims(res.data.claims ?? []);
      })
      .catch(() => {
        if (cancelled) return;
        setNmClaims([]);
        setNmClaimsError("加载节点声明失败。");
      })
      .finally(() => {
        if (!cancelled) setNmClaimsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    novelId,
    structureSource,
    nmVersionId,
    selectedNode?.nmNodeId,
    selectedNode?.chapterEnd,
    relationshipThroughChapter,
  ]);

  // Claims → source-links drill (node-level API; filter by selected claim client-side)
  useEffect(() => {
    if (
      !novelId ||
      structureSource !== "narrative_memory" ||
      !nmVersionId ||
      !selectedNode?.nmNodeId ||
      selectedClaimId == null
    ) {
      // 依赖切换时的同步重置；正规做法是 render 期派生或 key 重挂载，Phase 25 再收
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setNmSourceLinks([]);
      setNmSourceLinksLoading(false);
      setNmSourceLinksError(null);
      return;
    }
    let cancelled = false;
    setNmSourceLinksLoading(true);
    setNmSourceLinksError(null);
    const through =
      typeof relationshipThroughChapter === "number"
        ? relationshipThroughChapter
        : selectedNode.chapterEnd;
    narrativeMemoryApi
      .getSourceLinks(novelId, nmVersionId, selectedNode.nmNodeId, {
        through_chapter: through,
      })
      .then((res) => {
        if (cancelled) return;
        setNmSourceLinks(res.data.source_links ?? []);
      })
      .catch(() => {
        if (cancelled) return;
        setNmSourceLinks([]);
        setNmSourceLinksError("加载证据链接失败。");
      })
      .finally(() => {
        if (!cancelled) setNmSourceLinksLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    novelId,
    structureSource,
    nmVersionId,
    selectedNode?.nmNodeId,
    selectedNode?.chapterEnd,
    selectedClaimId,
    relationshipThroughChapter,
  ]);

  return {
    structureSource,
    structureForest,
    selectedNode,
    nmVersionId,
    nmClaims,
    nmClaimsLoading,
    nmClaimsError,
    selectedClaimId,
    nmSourceLinks,
    nmSourceLinksLoading,
    nmSourceLinksError,
    relationshipThroughChapter,
    /** 共享 ref：timeline hook 读取 chapterStart/chapterEnd 做服务端范围。 */
    selectedNodeRef,
    prepareForNovel,
    loadStructure,
    selectStructureNode,
    onClaimSelect: handleClaimSelect,
  };
}
