"use client";

/**
 * 全局分析工作台 — Phase 20 Structure Workspace
 * 结构为脊梁；时间线 / 人物关系 / 线索与伏笔为挂载在选中结构节点上的 facets。
 * 不暴露中间摘要/主题/节奏；NM 始终为 candidate_preview（预览·未发布）。
 */

import { useCallback, useEffect, useMemo, useRef, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { BookOpen, RefreshCw } from "lucide-react";
import { NovelPickerStrip } from "@/components/bookshelf/novel-picker-strip";

import {
  AnalysisChatPanel,
  type AnalysisChapterRef,
} from "@/components/analysis/analysis-chat-panel";
import { AgentWorkspacePanel } from "@/components/analysis/agent-workspace-panel";
import { ClueWorkspace } from "@/components/clues/clue-workspace";
import { clueApi } from "@/lib/clue-api";
import { RelationshipWorkspace } from "@/components/relationships/relationship-workspace";
import {
  buildChapterFallbackTree,
  buildNmStructureTree,
  findTreeNodeById,
  findTreeNodeByNmId,
  pickDefaultTreeNode,
  treeNodeToSelection,
} from "@/components/structure/build-structure-tree";
import { StructureWorkspaceShell } from "@/components/structure/structure-workspace-shell";
import {
  densifyTimelineForMultiChapter,
  eventInChapterRange,
  isMultiChapterScope,
  type StructureNodeSelection,
  type StructureSource,
  type StructureTreeNode,
} from "@/components/structure/structure-types";
import { TimelineChart } from "@/components/timeline/timeline-chart";
import { TimelineControls } from "@/components/timeline/timeline-controls";
import { TimelineStatus } from "@/components/timeline/timeline-status";
import {
  novelsApi,
  timelineApi,
  type Novel,
  type TimelineEnvelope,
  type TimelineEvent,
  type TimelineOrdering,
  type TimelineRun,
  type TimelineVersionSource,
} from "@/lib/api";
import {
  narrativeMemoryApi,
  pickLatestPreviewVersion,
  type NmClaimItem,
  type NmSourceLinkItem,
} from "@/lib/narrative-memory-api";
import { cn } from "@/lib/utils";

/** Soft cap for multi-chapter scatter — full set kept for list density notes. */
const MULTI_CHAPTER_TIMELINE_CAP = 120;

type AnalysisWorkspaceMode = "timeline" | "relationships" | "clues";

/** Phase 25.1-02：页面顶层视图 —— 对话（默认）| 分析可视化；25.2-04 新增 agent */
type AnalysisPageView = "chat" | "analysis" | "agent";

function eventsSignature(
  envelope: TimelineEnvelope,
  source: TimelineVersionSource
): string {
  const view = envelope[source] ?? envelope.active ?? envelope.running_candidate;
  if (!view) return "empty";
  const ids = view.events.map((e) => e.id).join(",");
  // include titles so progressive title fixes also refresh list/chart
  const titles = view.events.map((e) => e.title).join("|");
  return `${source}:${view.version_id}:${view.events.length}:${ids}:${titles}`;
}

const ACTIVE_RUN = new Set(["pending", "queued", "running", "partial"]);

/** While a run is live, always surface the candidate version so chart/list grow live. */
function resolveTimelineSource(
  data: TimelineEnvelope,
  preferred: TimelineVersionSource,
  runStatus: string | null | undefined
): TimelineVersionSource {
  const live = Boolean(runStatus && ACTIVE_RUN.has(runStatus));
  const hasActive = Boolean(data.active?.events?.length);
  const hasCandidate = Boolean(data.running_candidate?.events?.length);
  // Live run always prefers the growing candidate.
  if (live && data.running_candidate) {
    return "running_candidate";
  }
  // Prefer preferred only when it actually has events.
  if (preferred === "active" && hasActive) return "active";
  if (preferred === "running_candidate" && data.running_candidate) {
    return "running_candidate";
  }
  // Cancelled/failed runs often leave a rich candidate with no active pointer.
  if (hasCandidate && !hasActive) return "running_candidate";
  if (hasActive) return "active";
  if (data.running_candidate) return "running_candidate";
  if (data.active) return "active";
  return preferred;
}

function pickTimelineView(
  envelope: TimelineEnvelope,
  source: TimelineVersionSource
) {
  return (
    envelope[source] ??
    envelope.active ??
    envelope.running_candidate ??
    null
  );
}

function AnalysisWorkspace() {
  const searchParams = useSearchParams();
  const novelFromQuery = searchParams.get("novel") || "";

  const [novels, setNovels] = useState<Novel[]>([]);
  const [novelId, setNovelId] = useState("");
  const [ordering, setOrdering] = useState<TimelineOrdering>("narrative");
  const [person, setPerson] = useState("");
  const [causal, setCausal] = useState(false);
  const [fullBook, setFullBook] = useState(false);
  const [confirmFullBook, setConfirmFullBook] = useState(false);
  const [source, setSource] = useState<TimelineVersionSource>("active");
  const [workspace, setWorkspace] =
    useState<AnalysisWorkspaceMode>("timeline");
  /** 顶层视图：默认对话；切换只做 CSS 隐藏，不卸载另一视图的状态。 */
  const [pageView, setPageView] = useState<AnalysisPageView>("chat");
  /** 章节 id/章号映射（对话锚点与剧透边界提示复用结构树的章节数据）。 */
  const [chapterList, setChapterList] = useState<AnalysisChapterRef[]>([]);
  /** Shared narrative chapter for relationship fold (server remains spoiler authority). */
  const [throughChapter, setThroughChapter] = useState<number | "">("");
  /**
   * 用户显式设置的剧透上限（关系/时间线点击）。
   * 结构树只响应显式上限；节点选择对 throughChapter 的自动对齐
   * 是 facet 过滤用途，不应让导航树收缩（否则点一章树就少一截）。
   */
  const [explicitThrough, setExplicitThrough] = useState<number | "">("");
  /** Structure Workspace spine (Phase 20). */
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
  const [envelope, setEnvelope] = useState<TimelineEnvelope>({
    active: null,
    running_candidate: null,
  });
  const [run, setRun] = useState<TimelineRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const envelopeSigRef = useRef("");
  const progressSigRef = useRef("");
  const sourceRef = useRef(source);
  const runStatusRef = useRef<string | null>(null);
  /** Last through_chapter used for NM tree fetch (re-fetch on spoiler change). */
  const nmTreeThroughRef = useRef<number | null>(null);
  const selectedNodeRef = useRef<StructureNodeSelection | null>(null);
  /** 选中作品的章节真实标题（chapters 表），结构树标签统一用它补全章节名。 */
  const chapterTitlesRef = useRef<Record<number, string>>({});
  sourceRef.current = source;
  runStatusRef.current = run?.status ?? null;
  selectedNodeRef.current = selectedNode;

  const applyChapterForest = useCallback((chapterCount: number) => {
    const forest = buildChapterFallbackTree(Math.max(1, chapterCount || 1), {
      titles: chapterTitlesRef.current,
    });
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
      const chapterCount = novelMeta?.chapter_count ?? 1;
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
        const latest = pickLatestPreviewVersion(versionsRes.data.versions ?? []);
        if (!latest) {
          applyChapterForest(chapterCount);
          return;
        }
        const effectiveThrough =
          throughOverride !== undefined ? throughOverride : throughChapter;
        const through =
          typeof effectiveThrough === "number" && effectiveThrough >= 1
            ? effectiveThrough
            : chapterCount || undefined;
        const treeRes = await narrativeMemoryApi.getTree(id, latest.version_id, {
          through_chapter: through,
        });
        const forest = buildNmStructureTree(treeRes.data.nodes ?? [], {
          chapterTitles: chapterTitlesRef.current,
        });
        if (!forest.length) {
          applyChapterForest(chapterCount);
          return;
        }
        const resolvedThrough =
          typeof through === "number" && through >= 1 ? through : chapterCount;
        applyNmForest(forest, latest.version_id, resolvedThrough, {
          preserveSelection: opts?.preserveSelection,
        });
      } catch {
        // Honest fallback: no NM or API error → chapter structure only
        applyChapterForest(chapterCount);
      }
    },
    [applyChapterForest, applyNmForest, throughChapter]
  );

  useEffect(() => {
    novelsApi
      .list()
      .then((response) => setNovels(response.data.items))
      .catch(() => setError("无法加载小说列表"));
  }, []);

  useEffect(() => {
    if (!novelFromQuery || !novels.length) return;
    if (String(novelId) === String(novelFromQuery)) return;
    const exists = novels.some((n) => String(n.id) === String(novelFromQuery));
    if (exists) void selectNovel(String(novelFromQuery));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelFromQuery, novels]);

  useEffect(() => {
    if (!novelId || !run || !ACTIVE_RUN.has(run.status)) return;

    let cancelled = false;
    const tick = async () => {
      try {
        const statusResponse = await timelineApi.status(novelId);
        if (cancelled) return;
        const nextRun = statusResponse.data;
        setRun(nextRun);
        runStatusRef.current = nextRun.status;

        const progressKey = [
          nextRun.status,
          nextRun.progress?.completed_chapters,
          nextRun.progress?.total_chapters,
          nextRun.progress?.stage,
          nextRun.updated_at,
        ].join(":");
        const progressChanged = progressKey !== progressSigRef.current;
        progressSigRef.current = progressKey;

        // Progress change or live run → refresh envelope (candidate events grow).
        await loadTimeline(novelId, undefined, !progressChanged);
        if (cancelled) return;

        if (nextRun.status === "completed") {
          // promote view to active
          envelopeSigRef.current = "";
          await loadTimeline(novelId, undefined, false);
        }
      } catch {
        if (!cancelled) {
          setError("自动更新暂时中断，可重新选择小说重试。");
        }
      }
    };

    // Immediate tick so first chapter results appear without waiting a full interval
    void tick();
    const timer = window.setInterval(() => void tick(), 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelId, run?.status]);

  async function loadTimeline(
    id = novelId,
    next: {
      ordering: TimelineOrdering;
      person: string;
      causal: boolean;
      fullBook: boolean;
      chapterStart?: number;
      chapterEnd?: number;
    } = {
      ordering,
      person,
      causal,
      fullBook,
      chapterStart: selectedNodeRef.current?.chapterStart,
      chapterEnd: selectedNodeRef.current?.chapterEnd,
    },
    onlyIfEventsChanged = false
  ) {
    if (!id) return;
    // While a run is live we always request full_book so the active tab (if any)
    // is not stuck on reading-progress cutoff; candidate also ignores cutoff server-side.
    const live = Boolean(
      runStatusRef.current && ACTIVE_RUN.has(runStatusRef.current)
    );
    // Structure scope: optional chapter_start/end; server min() with spoiler.
    // Prefer explicit next range, else current selection ref (stable for poll ticks).
    const scopeStart =
      next.chapterStart ?? selectedNodeRef.current?.chapterStart;
    const scopeEnd = next.chapterEnd ?? selectedNodeRef.current?.chapterEnd;
    const hasScope =
      typeof scopeStart === "number" &&
      typeof scopeEnd === "number" &&
      scopeStart >= 1 &&
      scopeEnd >= scopeStart;
    const response = await timelineApi.getTimeline(id, {
      ordering: next.ordering,
      person: next.person || undefined,
      causal: next.causal,
      full_book: next.fullBook || live,
      ...(hasScope
        ? { chapter_start: scopeStart, chapter_end: scopeEnd }
        : {}),
    });
    const data = response.data;
    const nextSource = resolveTimelineSource(
      data,
      sourceRef.current,
      runStatusRef.current
    );
    const sig = eventsSignature(data, nextSource);
    if (onlyIfEventsChanged && sig === envelopeSigRef.current) {
      // still apply source switch if we should be on candidate while run is live
      if (nextSource !== sourceRef.current) {
        setSource(nextSource);
        sourceRef.current = nextSource;
      }
      return;
    }
    envelopeSigRef.current = sig;
    setEnvelope(data);
    if (nextSource !== sourceRef.current) {
      setSource(nextSource);
      sourceRef.current = nextSource;
    }
  }

  /** 仅选书 + 加载已有结果，不启动 worker；有数据则直接展示 */
  async function selectNovel(id: string) {
    setNovelId(id);
    setPerson("");
    setSource("active");
    setThroughChapter("");
    setExplicitThrough("");
    setWorkspace("timeline");
    setError("");
    envelopeSigRef.current = "";
    progressSigRef.current = "";
    runStatusRef.current = null;
    setNmClaims([]);
    setNmClaimsError(null);
    setSelectedClaimId(null);
    setNmSourceLinks([]);
    setNmSourceLinksError(null);
    setNmVersionId(null);
    nmTreeThroughRef.current = null;
    setChapterList([]);
    if (!id) {
      setEnvelope({ active: null, running_candidate: null });
      setRun(null);
      setFullBook(false);
      setStructureForest([]);
      chapterTitlesRef.current = {};
      selectedNodeRef.current = null;
      setSelectedNode(null);
      setStructureSource("chapters");
      return;
    }
    // 同步服务端全书偏好，避免选书后还要再勾一次才看见数据
    const novelMeta = novels.find((n) => String(n.id) === String(id));
    const preferFullBook = Boolean(
      novelMeta?.reading_progress?.timeline_full_book
    );
    setFullBook(preferFullBook);
    // Structure spine first (chapters fallback immediately, then NM if any)
    applyChapterForest(novelMeta?.chapter_count ?? 1);
    setLoading(true);
    try {
      void loadStructure(id, novelMeta, "");
      // 读已有时间线（若有）— 选书即上数据，不点「开始分析」
      try {
        await loadTimeline(
          id,
          { ordering, person: "", causal, fullBook: preferFullBook },
          false
        );
      } catch {
        setEnvelope({ active: null, running_candidate: null });
      }
      // 读任务状态；404 表示从未分析
      try {
        const statusResponse = await timelineApi.status(id);
        setRun(statusResponse.data);
        runStatusRef.current = statusResponse.data.status;
        const st = statusResponse.data.status;
        if (st === "completed") {
          setSource("active");
          sourceRef.current = "active";
          envelopeSigRef.current = "";
          await loadTimeline(
            id,
            { ordering, person: "", causal, fullBook: preferFullBook },
            false
          );
        } else if (ACTIVE_RUN.has(st)) {
          // Snap to candidate so existing partial results are visible immediately
          setSource("running_candidate");
          sourceRef.current = "running_candidate";
          envelopeSigRef.current = "";
          await loadTimeline(
            id,
            { ordering, person: "", causal, fullBook: preferFullBook },
            false
          );
        } else if (st === "cancelled") {
          setSource("running_candidate");
          sourceRef.current = "running_candidate";
          envelopeSigRef.current = "";
          await loadTimeline(
            id,
            { ordering, person: "", causal, fullBook: preferFullBook },
            false
          );
        } else if (st === "paused_dependency" || st === "paused_budget" || st === "failed") {
          setSource("running_candidate");
          sourceRef.current = "running_candidate";
          envelopeSigRef.current = "";
          await loadTimeline(
            id,
            { ordering, person: "", causal, fullBook: preferFullBook },
            false
          );
          if (statusResponse.data.status_reason) {
            setError(statusResponse.data.status_reason);
          }
        }
      } catch {
        setRun(null);
      }
    } catch {
      setError("加载小说分析状态失败。");
    } finally {
      setLoading(false);
    }
  }

  /** 用户点击后才 start-or-resume */
  async function startAnalysis() {
    if (!novelId) return;
    setLoading(true);
    setError("");
    try {
      // Product: timeline primary; clue starts in parallel (may pause until hierarchy/timeline ready).
      // Relationship worker is dispatched by backend after timeline promote.
      const [timelineStart] = await Promise.allSettled([
        timelineApi.startOrResume(novelId),
        clueApi.startOrResume(novelId),
      ]);
      if (timelineStart.status === "rejected") throw timelineStart.reason;
      const statusResponse = await timelineApi.status(novelId);
      setRun(statusResponse.data);
      runStatusRef.current = statusResponse.data.status;
      envelopeSigRef.current = "";
      progressSigRef.current = "";
      // Always show progressive candidate data immediately after start.
      setSource("running_candidate");
      sourceRef.current = "running_candidate";
      await loadTimeline(novelId, undefined, false);
      const st = statusResponse.data.status;
      if (st === "completed") {
        setSource("active");
        sourceRef.current = "active";
        await loadTimeline(novelId, undefined, false);
      }
    } catch (err: unknown) {
      const detail =
        err &&
        typeof err === "object" &&
        "response" in err &&
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail;
      setError(typeof detail === "string" ? detail : "启动分析失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }

  async function updateQuery(
    next: Partial<{
      ordering: TimelineOrdering;
      person: string;
      causal: boolean;
      fullBook: boolean;
    }>
  ) {
    const query = { ordering, person, causal, fullBook, ...next };
    if (next.ordering) setOrdering(next.ordering);
    if (next.person !== undefined) setPerson(next.person);
    if (next.causal !== undefined) setCausal(next.causal);
    if (next.fullBook !== undefined) setFullBook(next.fullBook);
    try {
      envelopeSigRef.current = "";
      await loadTimeline(novelId, query, false);
    } catch {
      setError("筛选结果更新失败。");
    }
  }

  async function enableFullBook() {
    setConfirmFullBook(false);
    await timelineApi.setFullBookPreference(novelId, true);
    await updateQuery({ fullBook: true });
  }

  async function pauseRun() {
    if (!novelId) return;
    setLoading(true);
    setError("");
    try {
      const res = await timelineApi.cancel(novelId);
      setRun(res.data);
    } catch {
      setError("暂停失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }

  async function retryRun() {
    // 与 start 同一路径：start-or-resume 会恢复 checkpoint
    await startAnalysis();
  }

  const selectedNovel = novels.find((novel) => String(novel.id) === novelId);
  const view = pickTimelineView(envelope, source);

  /**
   * Timeline is loaded with chapter_start/end when a structure node is selected
   * (server intersects spoiler cutoff). Client filter remains defense-in-depth.
   * Multi-chapter scopes densify (cap + per-chapter counts); single-chapter
   * keeps full Phase 19 swimlane UX.
   */
  const scopedEventsRaw: TimelineEvent[] = useMemo(() => {
    const events = view?.events ?? [];
    if (!selectedNode) return events;
    const { chapterStart, chapterEnd } = selectedNode;
    return events.filter((e) =>
      eventInChapterRange(e.narrative_chapter_number, chapterStart, chapterEnd)
    );
  }, [view, selectedNode]);

  const multiChapterScope = Boolean(
    selectedNode &&
      isMultiChapterScope(selectedNode.chapterStart, selectedNode.chapterEnd)
  );

  const timelineDensity = useMemo(() => {
    if (!multiChapterScope) {
      return {
        displayEvents: scopedEventsRaw,
        total: scopedEventsRaw.length,
        truncated: 0,
        byChapter: [] as { chapter: number; count: number }[],
      };
    }
    return densifyTimelineForMultiChapter(
      scopedEventsRaw,
      MULTI_CHAPTER_TIMELINE_CAP
    );
  }, [multiChapterScope, scopedEventsRaw]);

  const scopedEvents: TimelineEvent[] = timelineDensity.displayEvents;

  const scopedCausalEdges = useMemo(() => {
    if (!view || !selectedNode) return view?.causal_edges ?? [];
    const ids = new Set(scopedEvents.map((e) => e.id));
    return (view.causal_edges ?? []).filter(
      (edge) => ids.has(edge.source_event_id) && ids.has(edge.target_event_id)
    );
  }, [view, selectedNode, scopedEvents]);

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

  const people = useMemo(
    () =>
      Array.from(
        new Set(
          scopedEvents.flatMap((event) =>
            event.participants.map((item) => item.mention)
          )
        )
      ).sort(),
    [scopedEvents]
  );

  function selectSource(nextSource: TimelineVersionSource) {
    setSource(nextSource);
    if (person) void updateQuery({ person: "" });
  }

  function handleStructureSelect(node: StructureTreeNode) {
    const selection = treeNodeToSelection(node);
    setSelectedNode(selection);
    selectedNodeRef.current = selection;
    setSelectedClaimId(null);
    setNmSourceLinks([]);
    setNmSourceLinksError(null);
    // Align relationship through with node end when user has not set a lower cap
    if (throughChapter === "" || throughChapter > selection.chapterEnd) {
      setThroughChapter(selection.chapterEnd);
    }
    // Re-fetch timeline with server-side structure chapter range (progressive poll uses ref).
    if (novelId) {
      envelopeSigRef.current = "";
      void loadTimeline(
        novelId,
        {
          ordering,
          person,
          causal,
          fullBook,
          chapterStart: selection.chapterStart,
          chapterEnd: selection.chapterEnd,
        },
        false
      ).catch(() => {
        /* keep previous envelope on transient failure */
      });
    }
  }

  function handleClaimSelect(claim: NmClaimItem) {
    setSelectedClaimId((prev) => (prev === claim.id ? null : claim.id));
  }

  // Re-fetch NM tree only when the user sets an EXPLICIT spoiler cap
  // (mid-session). Auto-alignment from node selection must not shrink the tree.
  useEffect(() => {
    if (!novelId || structureSource !== "narrative_memory" || !nmVersionId) {
      return;
    }
    const chapterCount = selectedNovel?.chapter_count ?? 1;
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
    selectedNovel?.chapter_count,
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
      /* eslint-disable react-hooks/set-state-in-effect */
      setNmClaims([]);
      setNmClaimsError(null);
      setNmClaimsLoading(false);
      setSelectedClaimId(null);
      setNmSourceLinks([]);
      /* eslint-enable react-hooks/set-state-in-effect */
      return;
    }
    let cancelled = false;
    /* eslint-disable react-hooks/set-state-in-effect */
    setNmClaimsLoading(true);
    setNmClaimsError(null);
    setSelectedClaimId(null);
    setNmSourceLinks([]);
    setNmSourceLinksError(null);
    /* eslint-enable react-hooks/set-state-in-effect */
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
      /* eslint-disable react-hooks/set-state-in-effect */
      setNmSourceLinks([]);
      setNmSourceLinksLoading(false);
      setNmSourceLinksError(null);
      /* eslint-enable react-hooks/set-state-in-effect */
      return;
    }
    let cancelled = false;
    /* eslint-disable react-hooks/set-state-in-effect */
    setNmSourceLinksLoading(true);
    setNmSourceLinksError(null);
    /* eslint-enable react-hooks/set-state-in-effect */
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

  return (
    <div
      data-testid="analysis-fullpage"
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      {/* 顶栏：横向书脊选书条（视觉）+ 原生 select（无障碍/测试，视觉隐藏） */}
      <header className="flex shrink-0 items-end gap-3 border-b border-border/40 px-3 pt-2 sm:px-4">
        <h1 className="sr-only">结构工作台</h1>
        <NovelPickerStrip
          novels={novels}
          value={novelId}
          onSelect={(id) => void selectNovel(id)}
        />
        <label className="sr-only">
          选择小说
          <select
            aria-label="选择小说"
            value={novelId}
            onChange={(event) => void selectNovel(event.target.value)}
          >
            <option value="">请选择一本小说</option>
            {novels.map((novel) => (
              <option key={novel.id} value={novel.id}>
                {novel.title}
              </option>
            ))}
          </select>
        </label>
      </header>

      {!novelId ? (
        <div className="grid min-h-0 flex-1 place-items-center p-8 text-center motion-transition-content">
          <div>
            <BookOpen className="mx-auto mb-3 size-8 text-primary" />
            <h2 className="font-serif text-2xl font-semibold">
              选择一本小说
            </h2>
          </div>
        </div>
      ) : (
        <StructureWorkspaceShell
          className="min-h-0 flex-1"
          structureSource={structureSource}
          forest={structureForest}
          selected={selectedNode}
          onSelect={handleStructureSelect}
          claims={nmClaims}
          claimsLoading={nmClaimsLoading}
          claimsError={nmClaimsError}
          selectedClaimId={selectedClaimId}
          onClaimSelect={handleClaimSelect}
          sourceLinks={nmSourceLinks}
          sourceLinksLoading={nmSourceLinksLoading}
          sourceLinksError={nmSourceLinksError}
          novelId={novelId}
        >
          <div className="flex h-full min-h-0 flex-col gap-4">
            {/* 顶层视图切换：对话（默认）| 分析可视化 —— 切换仅 CSS 隐藏，不卸载另一视图 */}
            <div className="flex shrink-0 justify-center">
              <div
                role="tablist"
                aria-label="工作台视图"
                className="inline-flex gap-1 rounded-full border border-border/60 bg-card p-1 shadow-sm"
              >
                {(
                  [
                    ["chat", "对话"],
                    ["analysis", "分析"],
                    ["agent", "智能体"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={pageView === id}
                    data-testid={`analysis-view-tab-${id}`}
                    onClick={() => setPageView(id)}
                    className={`rounded-full px-4 py-1.5 text-sm transition-colors motion-duration-fast ${
                      pageView === id
                        ? "bg-foreground font-medium text-background shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* 对话视图（默认）：与阅读器聊天共享同一会话底座 */}
            <AnalysisChatPanel
              className={cn("min-h-0 flex-1", pageView !== "chat" && "hidden")}
              novelId={novelId}
              chapters={chapterList}
              fullBook={fullBook}
              progressChapterId={
                selectedNovel?.reading_progress?.chapter_id ?? null
              }
              selection={selectedNode}
            />

            {/* Agent Workspace（25.2-04）：SSE 流式技能运行；CSS-hide 保住流与产物状态 */}
            <AgentWorkspacePanel
              className={cn("min-h-0 flex-1", pageView !== "agent" && "hidden")}
              novelId={novelId}
              chapters={chapterList}
              fullBook={fullBook}
              progressChapterId={
                selectedNovel?.reading_progress?.chapter_id ?? null
              }
              selection={selectedNode}
            />

            {/* 分析可视化视图（隐藏时保留状态：facet tab / 筛选 / 数据） */}
            <div
              data-testid="analysis-visualization-view"
              className={cn("grid gap-4", pageView !== "analysis" && "hidden")}
            >
            {/* Facet tabs — 分段控件（画布切换），保持 tab 语义 */}
            <div className="flex justify-center">
              <div
                role="tablist"
                aria-label="分析切片"
                className="inline-flex gap-1 rounded-full border border-border/60 bg-card p-1 shadow-sm"
              >
                {(
                  [
                    ["timeline", "时间线"],
                    ["relationships", "人物关系"],
                    ["clues", "线索与伏笔"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={workspace === id}
                    onClick={() => setWorkspace(id)}
                    className={`rounded-full px-4 py-1.5 text-sm transition-colors motion-duration-fast ${
                      workspace === id
                        ? "bg-foreground font-medium text-background shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {workspace !== "clues" && (
              <TimelineStatus
                run={run}
                hasEvents={Boolean(
                  envelope.active?.events.length ||
                    envelope.running_candidate?.events.length
                )}
                onPause={() => void pauseRun()}
                onResume={() => void retryRun()}
                onStart={() => void startAnalysis()}
                actionBusy={loading}
              />
            )}

            {workspace !== "clues" && (
              <div className="grid gap-3">
                {workspace === "timeline" && (
                  <TimelineControls
                    ordering={ordering}
                    onOrderingChange={(value) =>
                      void updateQuery({ ordering: value })
                    }
                    people={people}
                    person={person}
                    onPersonChange={(value) =>
                      void updateQuery({ person: value })
                    }
                    causal={causal}
                    onCausalChange={(value) =>
                      void updateQuery({ causal: value })
                    }
                    fullBook={fullBook}
                    onFullBookRequest={(value) =>
                      value
                        ? setConfirmFullBook(true)
                        : void timelineApi
                            .setFullBookPreference(novelId, false)
                            .then(() => updateQuery({ fullBook: false }))
                    }
                  />
                )}
                {workspace === "relationships" && (
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                    <label className="flex items-center gap-2 text-amber-900/85">
                      <input
                        type="checkbox"
                        checked={fullBook}
                        onChange={(event) =>
                          event.target.checked
                            ? setConfirmFullBook(true)
                            : void timelineApi
                                .setFullBookPreference(novelId, false)
                                .then(() => updateQuery({ fullBook: false }))
                        }
                        className="rounded border-border"
                      />
                      显示全书（可能剧透）
                    </label>
                  </div>
                )}
                {(envelope.active || envelope.running_candidate) && (
                  <div
                    role="tablist"
                    aria-label="分析版本"
                    className="flex flex-wrap gap-3 text-xs"
                  >
                    {envelope.active && (
                      <button
                        role="tab"
                        aria-selected={source === "active"}
                        onClick={() => selectSource("active")}
                        className={`pb-0.5 ${
                          source === "active"
                            ? "font-medium text-foreground underline underline-offset-4"
                            : "text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        当前版本 · v{envelope.active.version_id}
                      </button>
                    )}
                    {envelope.running_candidate && (
                      <button
                        role="tab"
                        aria-selected={source === "running_candidate"}
                        onClick={() => selectSource("running_candidate")}
                        className={`inline-flex items-center gap-1 pb-0.5 ${
                          source === "running_candidate"
                            ? "font-medium text-foreground underline underline-offset-4"
                            : "text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        <RefreshCw className="size-3" />
                        {ACTIVE_RUN.has(run?.status ?? "")
                          ? "正在生成"
                          : "候选结果"}{" "}
                        · v{envelope.running_candidate.version_id}
                        {envelope.running_candidate.events?.length
                          ? ` · ${envelope.running_candidate.events.length} 事件`
                          : ""}
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}

            {workspace !== "clues" && error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}

            {/* 主区：时间轴 / 人物关系 / 线索（受结构节点章节范围约束） */}
            {workspace === "clues" ? (
              <ClueWorkspace
                key={novelId}
                novelId={novelId}
                fullBook={fullBook}
                chapterStart={selectedNode?.chapterStart ?? null}
                chapterEnd={selectedNode?.chapterEnd ?? null}
                onFullBookRequest={(enable) => {
                  if (enable) {
                    setConfirmFullBook(true);
                  } else {
                    void timelineApi
                      .setFullBookPreference(novelId, false)
                      .then(() => updateQuery({ fullBook: false }));
                  }
                }}
              />
            ) : workspace === "relationships" ? (
              <RelationshipWorkspace
                key={`${novelId}:${source}:${view?.version_id ?? "none"}:${relationshipThroughChapter}`}
                novelId={novelId}
                source={source}
                versionId={view?.version_id}
                fullBook={fullBook}
                throughChapter={relationshipThroughChapter}
                onThroughChapterChange={(value) => {
                  setThroughChapter(value);
                  setExplicitThrough(value);
                }}
                maxChapter={
                  selectedNode
                    ? Math.min(
                        selectedNode.chapterEnd,
                        selectedNovel?.chapter_count ?? selectedNode.chapterEnd
                      )
                    : selectedNovel?.chapter_count
                }
              />
            ) : loading && !view ? (
              <div
                className="grid h-96 min-h-96 place-items-center rounded-3xl bg-muted motion-transition-content"
                role="status"
                aria-busy="true"
                aria-label="正在加载时间线"
              >
                <p className="text-sm text-muted-foreground">正在加载时间线…</p>
              </div>
            ) : view ? (
              <>
                {multiChapterScope && selectedNode && timelineDensity.truncated > 0 && (
                  <p
                    data-testid="timeline-multi-chapter-density"
                    className="sr-only"
                  >
                    <span data-testid="timeline-density-truncated">
                      truncated {timelineDensity.truncated}
                    </span>
                  </p>
                )}
                <TimelineChart
                  events={scopedEvents}
                  causalEdges={causal ? scopedCausalEdges : []}
                  ordering={ordering}
                  novelId={novelId}
                  onNarrativePositionChange={(chapter) => {
                    if (chapter != null) {
                      setThroughChapter(chapter);
                      setExplicitThrough(chapter);
                    }
                  }}
                />
              </>
            ) : (
              <div className="grid min-h-48 place-items-center py-10 text-center text-muted-foreground">
                <div>
                  <p className="text-sm">暂无时间线事件</p>
                  <p className="mt-1.5 text-xs">
                    点「开始分析」后，按章事件会显示在这里
                  </p>
                  <button
                    type="button"
                    disabled={loading}
                    onClick={() => void startAnalysis()}
                    className="mt-4 rounded-md bg-foreground px-4 py-2 text-sm text-background disabled:opacity-50"
                  >
                    开始分析
                  </button>
                </div>
              </div>
            )}
            </div>
          </div>
        </StructureWorkspaceShell>
      )}

      {confirmFullBook && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="确认显示全书"
          className="fixed inset-0 z-50 grid place-items-center bg-black/45 p-4"
        >
          <div className="max-w-md rounded-3xl bg-background p-6 shadow-2xl">
            <h2 className="font-serif text-2xl font-semibold">确认显示全书</h2>
            <p className="mt-3 text-sm text-muted-foreground">
              这会显示阅读进度之后的事件，可能包含重大剧透。偏好将按本书保存。
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <button
                className="rounded-xl border px-4 py-2 text-sm"
                onClick={() => setConfirmFullBook(false)}
              >
                取消
              </button>
              <button
                className="rounded-xl bg-foreground px-4 py-2 text-sm text-background"
                onClick={() => void enableFullBook()}
              >
                确认显示全书
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function AnalysisPage() {
  return (
    <Suspense
      fallback={
        <div className="grid min-h-72 place-items-center text-sm text-muted-foreground">
          加载分析工作台…
        </div>
      }
    >
      <AnalysisWorkspace />
    </Suspense>
  );
}
