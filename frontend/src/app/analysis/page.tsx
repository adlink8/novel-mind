"use client";

/**
 * 全局分析工作台 — Phase 20 Structure Workspace
 * 结构为脊梁；时间线 / 人物关系 / 线索与伏笔为挂载在选中结构节点上的 facets。
 * 不暴露中间摘要/主题/节奏；NM 始终为 candidate_preview（预览·未发布）。
 */

import { useCallback, useEffect, useMemo, useRef, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { BookOpen, RefreshCw } from "lucide-react";

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

/** Soft cap for multi-chapter scatter — full set kept for list density notes. */
const MULTI_CHAPTER_TIMELINE_CAP = 120;

type AnalysisWorkspaceMode = "timeline" | "relationships" | "clues";

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
  /** Shared narrative chapter for relationship fold (server remains spoiler authority). */
  const [throughChapter, setThroughChapter] = useState<number | "">("");
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
  const [prepNote, setPrepNote] = useState("");
  const envelopeSigRef = useRef("");
  const progressSigRef = useRef("");
  const sourceRef = useRef(source);
  const runStatusRef = useRef<string | null>(null);
  /** Last through_chapter used for NM tree fetch (re-fetch on spoiler change). */
  const nmTreeThroughRef = useRef<number | null>(null);
  const selectedNodeRef = useRef<StructureNodeSelection | null>(null);
  sourceRef.current = source;
  runStatusRef.current = run?.status ?? null;
  selectedNodeRef.current = selectedNode;

  const applyChapterForest = useCallback((chapterCount: number) => {
    const forest = buildChapterFallbackTree(Math.max(1, chapterCount || 1));
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
    setSelectedNode(def ? treeNodeToSelection(def) : null);
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

      if (opts?.preserveSelection) {
        const prev = selectedNodeRef.current;
        if (prev?.nmNodeId != null) {
          const found = findTreeNodeByNmId(forest, prev.nmNodeId);
          if (found) {
            setSelectedNode(treeNodeToSelection(found));
            return;
          }
        }
        if (prev?.id) {
          const found = findTreeNodeById(forest, prev.id);
          if (found) {
            setSelectedNode(treeNodeToSelection(found));
            return;
          }
        }
      }
      const def = pickDefaultTreeNode(forest);
      setSelectedNode(def ? treeNodeToSelection(def) : null);
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
        const versionsRes = await narrativeMemoryApi.listVersions(id);
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
        const forest = buildNmStructureTree(treeRes.data.nodes ?? []);
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

        if (ACTIVE_RUN.has(nextRun.status)) {
          setPrepNote(
            `分析进行中：${Number(nextRun.progress?.completed_chapters ?? 0)}/${Number(nextRun.progress?.total_chapters ?? 0) || "?"} 章 · 图与列表会随章节更新`
          );
        } else if (nextRun.status === "completed") {
          setPrepNote("时间线已就绪。");
          // promote view to active
          envelopeSigRef.current = "";
          await loadTimeline(novelId, undefined, false);
        } else if (nextRun.status === "cancelled") {
          setPrepNote("分析已暂停，已完成章节结果会保留。");
        } else if (
          nextRun.status === "paused_budget" ||
          nextRun.status === "paused_dependency" ||
          nextRun.status === "failed"
        ) {
          setPrepNote(nextRun.status_reason || "分析已中断，可点「继续分析」。");
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
    next = { ordering, person, causal, fullBook },
    onlyIfEventsChanged = false
  ) {
    if (!id) return;
    // While a run is live we always request full_book so the active tab (if any)
    // is not stuck on reading-progress cutoff; candidate also ignores cutoff server-side.
    const live = Boolean(
      runStatusRef.current && ACTIVE_RUN.has(runStatusRef.current)
    );
    const response = await timelineApi.getTimeline(id, {
      ordering: next.ordering,
      person: next.person || undefined,
      causal: next.causal,
      full_book: next.fullBook || live,
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
    setWorkspace("timeline");
    setError("");
    setPrepNote("");
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
    if (!id) {
      setEnvelope({ active: null, running_candidate: null });
      setRun(null);
      setFullBook(false);
      setStructureForest([]);
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
          setPrepNote(
            "已有时间线数据，可直接浏览。人物关系可看共现临时图（正式关系观察待知识图谱）；线索可在「线索与伏笔」查看或重试。需要可点「重新分析」。"
          );
        } else if (ACTIVE_RUN.has(st)) {
          setPrepNote(
            "检测到进行中的时间线：事件会陆续出现。线索已可并行；人物关系在时间线发布后自动跑，图可先看共现。"
          );
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
          setPrepNote(
            "上次分析已暂停。若有候选事件可先浏览；点「继续分析」续跑。完成后人物关系与线索会并行生成。"
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
          setPrepNote("上次分析中断，可点「继续分析」重试。关系/线索在时间线就绪后并行。");
          if (statusResponse.data.status_reason) {
            setError(statusResponse.data.status_reason);
          }
        } else {
          setPrepNote("已加载状态。点「开始分析」：时间线主跑，同时并行线索；关系在时间线发布后并行。");
        }
      } catch {
        setRun(null);
        setPrepNote("尚未分析。点「开始分析」后：时间线开始，并并行线索；关系在时间线发布后启动。");
      }
    } catch {
      setError("加载小说分析状态失败。");
      setPrepNote("");
    } finally {
      setLoading(false);
    }
  }

  /** 用户点击后才 start-or-resume */
  async function startAnalysis() {
    if (!novelId) return;
    setLoading(true);
    setError("");
    setPrepNote("正在启动分析：时间线入队；完成后并行人物关系与线索…");
    try {
      // Product: timeline primary; clue starts in parallel (may pause until hierarchy/timeline ready).
      // Relationship worker is dispatched by backend after timeline promote; graph may show
      // progressive co-occurrence while observations are empty.
      const [timelineStart, clueStart] = await Promise.allSettled([
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
        setPrepNote(
          "时间线已完成并发布。人物关系与线索已/将并行处理；关系图可先显示时间线共现临时边。"
        );
      } else if (ACTIVE_RUN.has(st)) {
        setPrepNote(
          clueStart.status === "fulfilled"
            ? "时间线分析中：候选事件会陆续出现。线索已并行入队；人物关系在时间线发布后自动跑，图可先看共现临时数据。"
            : "时间线分析中：候选事件会陆续出现。线索入队失败时可在「线索与伏笔」重试。"
        );
      } else {
        setPrepNote(statusResponse.data.status_reason || "任务已提交。");
      }
    } catch (err: unknown) {
      const detail =
        err &&
        typeof err === "object" &&
        "response" in err &&
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail;
      setError(typeof detail === "string" ? detail : "启动分析失败，请稍后重试。");
      setPrepNote("");
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
      setPrepNote(
        `已暂停。已完成约 ${Number(res.data.progress?.completed_chapters ?? 0)} 章结果会保留。`
      );
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
   * Timeline range filter is client-side only: server lacks
   * chapter_start..chapter_end params. When a structure node is selected,
   * keep events whose narrative_chapter_number is in [start, end].
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
    setSelectedClaimId(null);
    setNmSourceLinks([]);
    setNmSourceLinksError(null);
    // Align relationship through with node end when user has not set a lower cap
    if (throughChapter === "" || throughChapter > selection.chapterEnd) {
      setThroughChapter(selection.chapterEnd);
    }
  }

  function handleClaimSelect(claim: NmClaimItem) {
    setSelectedClaimId((prev) => (prev === claim.id ? null : claim.id));
  }

  // Re-fetch NM tree when spoiler through_chapter changes (mid-session).
  useEffect(() => {
    if (!novelId || structureSource !== "narrative_memory" || !nmVersionId) {
      return;
    }
    const chapterCount = selectedNovel?.chapter_count ?? 1;
    const through =
      typeof throughChapter === "number" && throughChapter >= 1
        ? throughChapter
        : chapterCount;
    if (nmTreeThroughRef.current === through) return;
    let cancelled = false;
    (async () => {
      try {
        const treeRes = await narrativeMemoryApi.getTree(novelId, nmVersionId, {
          through_chapter: through,
        });
        if (cancelled) return;
        const forest = buildNmStructureTree(treeRes.data.nodes ?? []);
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
    // selectedNovel chapter_count only — avoid novel object identity churn
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    novelId,
    structureSource,
    nmVersionId,
    throughChapter,
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

  return (
    <div className="mx-auto grid w-full max-w-[1500px] gap-3 px-4 py-5 sm:px-6 lg:px-8">
      {/* 顶栏：标题 + 选书 */}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-serif text-2xl font-semibold sm:text-3xl">
            结构工作台
          </h1>
          <p className="mt-0.5 text-xs text-muted-foreground sm:text-sm">
            结构为轴 · 时间线 / 人物关系 / 线索为切片 · 防剧透 · 点「开始分析」才调用模型
          </p>
        </div>
        <label className="grid min-w-48 gap-1 text-xs text-muted-foreground">
          选择小说
          <select
            aria-label="选择小说"
            value={novelId}
            onChange={(event) => void selectNovel(event.target.value)}
            className="h-10 rounded-xl border bg-card px-3 text-sm text-foreground"
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
        <div className="grid min-h-72 place-items-center rounded-3xl border border-dashed bg-card/50 p-8 text-center motion-transition-content">
          <div>
            <BookOpen className="mx-auto mb-3 size-8 text-primary" />
            <h2 className="font-serif text-2xl font-semibold">
              选择一本小说
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              进入后不会自动分析。确认后请点「开始分析」再跑时间线抽取。
            </p>
          </div>
        </div>
      ) : (
        <StructureWorkspaceShell
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
          <div className="grid gap-3">
            {/* Facet tabs: timeline | relationships | clues */}
            <div
              role="tablist"
              aria-label="分析切片"
              className="flex flex-wrap gap-2"
            >
              <button
                type="button"
                role="tab"
                aria-selected={workspace === "timeline"}
                onClick={() => setWorkspace("timeline")}
                className={`rounded-full px-4 py-2 text-sm transition-[color,background-color,border-color,box-shadow] motion-duration-standard motion-ease-enter ${
                  workspace === "timeline"
                    ? "bg-foreground text-background"
                    : "border bg-card"
                }`}
              >
                时间线
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={workspace === "relationships"}
                onClick={() => setWorkspace("relationships")}
                className={`rounded-full px-4 py-2 text-sm transition-[color,background-color,border-color,box-shadow] motion-duration-standard motion-ease-enter ${
                  workspace === "relationships"
                    ? "bg-foreground text-background"
                    : "border bg-card"
                }`}
              >
                人物关系
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={workspace === "clues"}
                onClick={() => setWorkspace("clues")}
                className={`rounded-full px-4 py-2 text-sm transition-[color,background-color,border-color,box-shadow] motion-duration-standard motion-ease-enter ${
                  workspace === "clues"
                    ? "bg-foreground text-background"
                    : "border bg-card"
                }`}
              >
                线索与伏笔
              </button>
            </div>

            {/* 线索工作台自带 run 状态与全书控件；时间线/关系保留既有顶栏 */}
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
              <div className="grid gap-2 rounded-2xl border bg-card/60 p-2 sm:p-3">
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
                  <div className="flex flex-wrap items-center gap-3 p-1">
                    <label className="flex h-10 items-center gap-2 rounded-xl border border-amber-300/70 bg-amber-50 px-3 text-sm text-amber-950">
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
                      />
                      显示全书（可能剧透）
                    </label>
                    <p className="text-xs text-muted-foreground">
                      与时间线共用版本与全书偏好；结构节点会收窄 through_chapter。
                    </p>
                  </div>
                )}
                {(envelope.active || envelope.running_candidate) && (
                  <div
                    role="tablist"
                    aria-label="分析版本"
                    className="flex gap-2 overflow-x-auto px-1"
                  >
                    {envelope.active && (
                      <button
                        role="tab"
                        aria-selected={source === "active"}
                        onClick={() => selectSource("active")}
                        className={`whitespace-nowrap rounded-full px-3 py-1.5 text-xs ${
                          source === "active"
                            ? "bg-foreground text-background"
                            : "border bg-background"
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
                        className={`whitespace-nowrap rounded-full px-3 py-1.5 text-xs ${
                          source === "running_candidate"
                            ? "bg-foreground text-background"
                            : "border bg-background"
                        }`}
                      >
                        <RefreshCw className="mr-1 inline size-3" />
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

            {workspace !== "clues" &&
              run &&
              ACTIVE_RUN.has(run.status) && (
              <p className="rounded-xl border border-sky-300/70 bg-sky-50 px-3 py-2 text-xs text-sky-950">
                分析进行中：进度 {Number(run.progress?.completed_chapters ?? 0)}/
                {Number(run.progress?.total_chapters ?? 0) || "?"} 章；时间线图/列表展示
                <strong> 候选 </strong>
                版本中已落库的全部事件（不受阅读进度截断）。当前可见{" "}
                {scopedEvents.length}
                {selectedNode && view && scopedEvents.length !== view.events.length
                  ? ` / 结构前 ${view.events.length}`
                  : ""}{" "}
                条。
              </p>
            )}
            {workspace !== "clues" &&
              run &&
              (run.status === "cancelled" || run.status === "failed") &&
              Boolean(envelope.running_candidate?.events?.length) && (
              <p className="rounded-xl border border-sky-300/70 bg-sky-50 px-3 py-2 text-xs text-sky-950">
                已暂停/中断，但仍有{" "}
                <strong>{envelope.running_candidate?.events.length ?? 0}</strong>{" "}
                条候选时间线事件可浏览。点「继续分析」可续跑；
                <strong>人物关系</strong>与<strong>线索</strong>
                要在时间线<strong>完成并发布</strong>后才会自动生成。
              </p>
            )}
            {workspace === "relationships" &&
              !(view?.events?.length) &&
              !(envelope.active || envelope.running_candidate) && (
              <p className="rounded-xl border border-amber-300/70 bg-amber-50 px-3 py-2 text-xs text-amber-950">
                人物关系依赖时间线版本。请先在「时间线」完成分析；完成后系统会自动抽取关系观察。
              </p>
            )}
            {workspace !== "clues" &&
              !ACTIVE_RUN.has(run?.status ?? "") &&
              source === "active" &&
              !fullBook &&
              !selectedNovel?.reading_progress?.timeline_full_book && (
              <p className="rounded-xl border border-amber-300/70 bg-amber-50 px-3 py-2 text-xs text-amber-950">
                防剧透：未勾选「显示全书」时，已发布版本只显示到阅读进度章节（无进度则仅第一章）。
                后台可能已分析更多章；勾选「显示全书」可看全部。候选结果页签不受阅读进度截断。
              </p>
            )}
            {workspace !== "clues" && prepNote && (
              <p className="text-xs text-muted-foreground">{prepNote}</p>
            )}
            {workspace !== "clues" && error && (
              <p
                role="alert"
                className="rounded-xl bg-destructive/10 p-3 text-sm text-destructive"
              >
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
                onThroughChapterChange={setThroughChapter}
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
                {multiChapterScope && selectedNode && (
                  <div
                    data-testid="timeline-multi-chapter-density"
                    className="rounded-xl border border-sky-300/70 bg-sky-50/80 px-3 py-2 text-xs text-sky-950"
                  >
                    <p className="font-medium">
                      多章聚合视图 · 第 {selectedNode.chapterStart}–
                      {selectedNode.chapterEnd} 章
                    </p>
                    <p className="mt-0.5 text-sky-900/85">
                      范围内共 {timelineDensity.total} 条事件
                      {timelineDensity.byChapter.length > 0 && (
                        <>
                          {" "}
                          · 分章：
                          {timelineDensity.byChapter
                            .slice(0, 12)
                            .map((c) => `第${c.chapter}章 ${c.count}`)
                            .join(" · ")}
                          {timelineDensity.byChapter.length > 12
                            ? ` · …共 ${timelineDensity.byChapter.length} 章`
                            : ""}
                        </>
                      )}
                      {timelineDensity.truncated > 0 && (
                        <span data-testid="timeline-density-truncated">
                          {" "}
                          · 图中展示 {scopedEvents.length} 条，还有{" "}
                          {timelineDensity.truncated} 条未展开
                        </span>
                      )}
                      。单章节点可看完整泳道。
                    </p>
                  </div>
                )}
                <TimelineChart
                  events={scopedEvents}
                  causalEdges={causal ? scopedCausalEdges : []}
                  ordering={ordering}
                  novelId={novelId}
                  onNarrativePositionChange={(chapter) => {
                    if (chapter != null) setThroughChapter(chapter);
                  }}
                />
              </>
            ) : (
              <div className="grid min-h-64 place-items-center rounded-3xl border border-dashed p-8 text-center text-muted-foreground">
                <div>
                  <p className="text-sm">暂无时间线事件。</p>
                  <p className="mt-2 text-xs">
                    点上方「开始分析」后，按章抽取的事件会显示在这里。
                  </p>
                  <button
                    type="button"
                    disabled={loading}
                    onClick={() => void startAnalysis()}
                    className="mt-4 rounded-xl bg-foreground px-5 py-2.5 text-sm text-background disabled:opacity-50"
                  >
                    开始分析
                  </button>
                </div>
              </div>
            )}
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
