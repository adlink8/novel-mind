"use client";

/**
 * 时间线工作台数据流 hook。
 *
 * 从「全局分析工作台」页面拆分而来：负责选书、时间线加载/轮询、
 * 分析启停、筛选与版本源切换，以及时间线画布/列表的派生数据
 * （章节范围裁剪、多章密度、因果边、人物选项）。
 *
 * 与 use-structure-forest.ts 的边界：
 * - 本 hook 只读共享的 selectedNodeRef（服务端章节范围），由结构 hook 持有并写入；
 * - selectNovel 通过 structure 参数回调结构 hook 的 prepareForNovel / loadStructure；
 * - envelopeSigRef 由本 hook 持有，页面 handleStructureSelect 通过 resetEnvelopeSig() 复位。
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import type { AnalysisWorkspaceMode } from "@/components/analysis/workspace-view-tabs";
import {
  densifyTimelineForMultiChapter,
  eventInChapterRange,
  isMultiChapterScope,
  type StructureNodeSelection,
} from "@/components/structure/structure-types";
import { clueApi } from "@/lib/clue-api";
import {
  timelineApi,
  type Novel,
  type TimelineEnvelope,
  type TimelineEvent,
  type TimelineOrdering,
  type TimelineRun,
  type TimelineVersionSource,
} from "@/lib/api";
import {
  ACTIVE_RUN,
  eventsSignature,
  pickTimelineView,
  resolveTimelineSource,
} from "@/lib/timeline-source";

/** Soft cap for multi-chapter scatter — full set kept for list density notes. */
const MULTI_CHAPTER_TIMELINE_CAP = 120;

type TimelineQueryNext = {
  ordering: TimelineOrdering;
  person: string;
  causal: boolean;
  fullBook: boolean;
  chapterStart?: number;
  chapterEnd?: number;
};

export function useTimelineWorkspace(params: {
  novelId: string;
  novels: Novel[];
  setNovelId: (id: string) => void;
  setThroughChapter: Dispatch<SetStateAction<number | "">>;
  setExplicitThrough: Dispatch<SetStateAction<number | "">>;
  setWorkspace: Dispatch<SetStateAction<AnalysisWorkspaceMode>>;
  setError: (message: string) => void;
  /** 结构 hook 持有并写入；loadTimeline 读取它做服务端章节范围。 */
  selectedNodeRef: MutableRefObject<StructureNodeSelection | null>;
  selectedNode: StructureNodeSelection | null;
  structure: {
    prepareForNovel: (id: string, novelMeta: Novel | undefined) => void;
    loadStructure: (
      id: string,
      novelMeta: Novel | undefined,
      throughOverride?: number | "",
      opts?: { preserveSelection?: boolean }
    ) => Promise<void>;
  };
}) {
  const {
    novelId,
    novels,
    setNovelId,
    setThroughChapter,
    setExplicitThrough,
    setWorkspace,
    setError,
    selectedNodeRef,
    selectedNode,
    structure,
  } = params;

  const [ordering, setOrdering] = useState<TimelineOrdering>("narrative");
  const [person, setPerson] = useState("");
  const [causal, setCausal] = useState(false);
  const [fullBook, setFullBook] = useState(false);
  const [confirmFullBook, setConfirmFullBook] = useState(false);
  const [source, setSource] = useState<TimelineVersionSource>("active");
  const [envelope, setEnvelope] = useState<TimelineEnvelope>({
    active: null,
    running_candidate: null,
  });
  const [run, setRun] = useState<TimelineRun | null>(null);
  const [loading, setLoading] = useState(false);
  const envelopeSigRef = useRef("");
  const progressSigRef = useRef("");
  const sourceRef = useRef(source);
  const runStatusRef = useRef<string | null>(null);
  sourceRef.current = source;
  runStatusRef.current = run?.status ?? null;

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
    next: TimelineQueryNext = {
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
    const scopeStart = next.chapterStart ?? selectedNodeRef.current?.chapterStart;
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
    const novelMeta = novels.find((n) => String(n.id) === String(id));
    structure.prepareForNovel(id, novelMeta);
    if (!id) {
      setEnvelope({ active: null, running_candidate: null });
      setRun(null);
      setFullBook(false);
      return;
    }
    // 同步服务端全书偏好，避免选书后还要再勾一次才看见数据
    const preferFullBook = Boolean(
      novelMeta?.reading_progress?.timeline_full_book
    );
    setFullBook(preferFullBook);
    setLoading(true);
    try {
      // The structure load resolves the real chapter coordinates and updates
      // selectedNodeRef. Do not issue the first timeline read against the
      // temporary 1..N fallback created during the book switch.
      await structure.loadStructure(id, novelMeta, "");
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
        } else if (
          st === "paused_dependency" ||
          st === "paused_budget" ||
          st === "failed"
        ) {
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
      setError(
        typeof detail === "string" ? detail : "启动分析失败，请稍后重试。"
      );
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

  function selectSource(nextSource: TimelineVersionSource) {
    setSource(nextSource);
    if (person) void updateQuery({ person: "" });
  }

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

  return {
    // state
    ordering,
    person,
    causal,
    fullBook,
    confirmFullBook,
    setConfirmFullBook,
    source,
    envelope,
    run,
    loading,
    // derived
    view,
    multiChapterScope,
    timelineDensity,
    scopedEvents,
    scopedCausalEdges,
    people,
    // actions
    selectNovel,
    loadTimeline,
    startAnalysis,
    updateQuery,
    enableFullBook,
    pauseRun,
    retryRun,
    selectSource,
    /** 页面 handleStructureSelect 复用：结构选中后强制重取时间线。 */
    resetEnvelopeSig: () => {
      envelopeSigRef.current = "";
    },
  };
}
