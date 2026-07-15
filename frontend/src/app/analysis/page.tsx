"use client";

/**
 * 全局分析工作台 — Phase 08 时间线 + Phase 09 人物关系
 * 布局 C1：状态+筛选紧凑 · 主图 · 详情抽屉 · 列表折叠
 * 工作区切换：timeline | relationships（不暴露中间摘要）
 */

import { useEffect, useMemo, useRef, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { BookOpen, RefreshCw } from "lucide-react";

import { RelationshipWorkspace } from "@/components/relationships/relationship-workspace";
import { TimelineChart } from "@/components/timeline/timeline-chart";
import { TimelineControls } from "@/components/timeline/timeline-controls";
import { TimelineStatus } from "@/components/timeline/timeline-status";
import {
  novelsApi,
  timelineApi,
  type Novel,
  type TimelineEnvelope,
  type TimelineOrdering,
  type TimelineRun,
  type TimelineVersionSource,
} from "@/lib/api";

type AnalysisWorkspaceMode = "timeline" | "relationships";

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
  if (live && data.running_candidate) {
    return "running_candidate";
  }
  if (!live && data.active) {
    return "active";
  }
  if (data[preferred]) return preferred;
  if (data.active) return "active";
  if (data.running_candidate) return "running_candidate";
  return preferred;
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
  sourceRef.current = source;
  runStatusRef.current = run?.status ?? null;

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

  /** 仅选书 + 加载已有结果，不启动 worker */
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
    if (!id) {
      setEnvelope({ active: null, running_candidate: null });
      setRun(null);
      return;
    }
    setLoading(true);
    try {
      // 读已有时间线（若有）
      try {
        await loadTimeline(id, undefined, false);
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
          setPrepNote("已有完成的时间线结果。可浏览；需要可点「重新分析」。");
        } else if (ACTIVE_RUN.has(st)) {
          setPrepNote("检测到进行中的任务：图与列表会自动刷新。未点「开始分析」不会新建任务。");
          // Snap to candidate so existing partial results are visible immediately
          setSource("running_candidate");
          sourceRef.current = "running_candidate";
          envelopeSigRef.current = "";
          await loadTimeline(id, undefined, false);
        } else if (st === "cancelled") {
          setPrepNote("上次分析已暂停，可点「继续分析」续跑。");
        } else if (st === "paused_dependency" || st === "paused_budget" || st === "failed") {
          setPrepNote("上次分析中断，可点「继续分析」重试。");
          if (statusResponse.data.status_reason) {
            setError(statusResponse.data.status_reason);
          }
        } else {
          setPrepNote("已加载状态。需要抽取时请点「开始分析」。");
        }
      } catch {
        setRun(null);
        setPrepNote("尚未分析。选择后请点「开始分析」才会调用模型。");
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
    setPrepNote("正在启动分析（准备场景层级并排队任务）…");
    try {
      await timelineApi.startOrResume(novelId);
      const statusResponse = await timelineApi.status(novelId);
      setRun(statusResponse.data);
      runStatusRef.current = statusResponse.data.status;
      envelopeSigRef.current = "";
      progressSigRef.current = "";
      // Prefer candidate tab while worker runs so chart/list stream in
      if (ACTIVE_RUN.has(statusResponse.data.status)) {
        setSource("running_candidate");
        sourceRef.current = "running_candidate";
      }
      await loadTimeline(novelId, undefined, false);
      const st = statusResponse.data.status;
      if (st === "completed") setPrepNote("时间线已就绪。");
      else if (ACTIVE_RUN.has(st)) {
        setPrepNote("分析进行中：图与列表按章实时更新，可随时暂停。");
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
  const view = envelope[source];
  const people = useMemo(
    () =>
      Array.from(
        new Set(
          (view?.events ?? []).flatMap((event) =>
            event.participants.map((item) => item.mention)
          )
        )
      ).sort(),
    [view]
  );

  function selectSource(nextSource: TimelineVersionSource) {
    setSource(nextSource);
    if (person) void updateQuery({ person: "" });
  }

  return (
    <div className="mx-auto grid w-full max-w-[1500px] gap-3 px-4 py-5 sm:px-6 lg:px-8">
      {/* 顶栏：标题 + 选书 */}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-serif text-2xl font-semibold sm:text-3xl">
            小说分析
          </h1>
          <p className="mt-0.5 text-xs text-muted-foreground sm:text-sm">
            时间线与人物关系共享版本与防剧透设置 · 点「开始分析」才调用模型
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
        <div className="grid min-h-72 place-items-center rounded-3xl border border-dashed bg-card/50 p-8 text-center">
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
        <div className="grid gap-3">
          {/* 第一行：状态 + 暂停/继续 */}
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

          {/* 工作区：仅时间线 / 人物关系（不暴露中间摘要） */}
          <div
            role="tablist"
            aria-label="分析工作区"
            className="flex flex-wrap gap-2"
          >
            <button
              type="button"
              role="tab"
              aria-selected={workspace === "timeline"}
              onClick={() => setWorkspace("timeline")}
              className={`rounded-full px-4 py-2 text-sm ${
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
              className={`rounded-full px-4 py-2 text-sm ${
                workspace === "relationships"
                  ? "bg-foreground text-background"
                  : "border bg-card"
              }`}
            >
              人物关系
            </button>
          </div>

          {/* 第二行：筛选工具条 + 版本（时间线控件；全书偏好两个工作区共享） */}
          <div className="grid gap-2 rounded-2xl border bg-card/60 p-2 sm:p-3">
            {workspace === "timeline" && (
              <TimelineControls
                ordering={ordering}
                onOrderingChange={(value) => void updateQuery({ ordering: value })}
                people={people}
                person={person}
                onPersonChange={(value) => void updateQuery({ person: value })}
                causal={causal}
                onCausalChange={(value) => void updateQuery({ causal: value })}
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
                  与时间线共用版本与全书偏好；API 为防剧透权威。
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
                    正在生成 · v{envelope.running_candidate.version_id}
                  </button>
                )}
              </div>
            )}
          </div>

          {run && ACTIVE_RUN.has(run.status) && (
            <p className="rounded-xl border border-sky-300/70 bg-sky-50 px-3 py-2 text-xs text-sky-950">
              分析进行中：进度 {Number(run.progress?.completed_chapters ?? 0)}/
              {Number(run.progress?.total_chapters ?? 0) || "?"} 章；时间线图/列表展示
              <strong> 正在生成 </strong>
              版本中已落库的全部事件（不受阅读进度截断）。当前可见{" "}
              {view?.events.length ?? 0} 条。
            </p>
          )}
          {!ACTIVE_RUN.has(run?.status ?? "") &&
            !fullBook &&
            !selectedNovel?.reading_progress?.timeline_full_book && (
            <p className="rounded-xl border border-amber-300/70 bg-amber-50 px-3 py-2 text-xs text-amber-950">
              防剧透：未勾选「显示全书」时，已发布版本只显示到阅读进度章节（无进度则仅第一章）。
              后台可能已分析更多章；勾选「显示全书」可看全部。
            </p>
          )}
          {prepNote && (
            <p className="text-xs text-muted-foreground">{prepNote}</p>
          )}
          {error && (
            <p
              role="alert"
              className="rounded-xl bg-destructive/10 p-3 text-sm text-destructive"
            >
              {error}
            </p>
          )}

          {/* 主区：时间轴 或 人物关系 */}
          {workspace === "relationships" ? (
            <RelationshipWorkspace
              key={`${novelId}:${source}:${view?.version_id ?? "none"}`}
              novelId={novelId}
              source={source}
              versionId={view?.version_id}
              fullBook={fullBook}
              throughChapter={throughChapter}
              onThroughChapterChange={setThroughChapter}
              maxChapter={selectedNovel?.chapter_count}
            />
          ) : loading && !view ? (
            <div
              className="h-96 animate-pulse rounded-3xl bg-muted"
              aria-label="正在加载时间线"
            />
          ) : view ? (
            <TimelineChart
              events={view.events}
              causalEdges={causal ? view.causal_edges : []}
              ordering={ordering}
              novelId={novelId}
              onNarrativePositionChange={(chapter) => {
                if (chapter != null) setThroughChapter(chapter);
              }}
            />
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
