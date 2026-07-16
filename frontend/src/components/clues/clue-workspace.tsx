"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  CLUE_ACTIVE_RUN,
  clueApi,
  sortVisibleClues,
  type ClueDetailPanels,
  type ClueEnvelope,
  type ClueLinkTargetKind,
  type ClueRun,
  type ClueState,
  type ClueVersionSource,
  type VisibleClue,
} from "@/lib/clue-api";
import { clueIntersectsChapterRange } from "@/components/structure/structure-types";
import { ClueBand } from "./clue-band";
import { ClueControls } from "./clue-controls";
import { ClueEvidencePanel } from "./clue-evidence-panel";

type Props = {
  novelId: string;
  fullBook: boolean;
  /** Parent owns Phase 08 confirmation + timeline_full_book persistence. */
  onFullBookRequest: (enable: boolean) => void;
  /**
   * Structure Workspace chapter scope (Phase 20). When set, client-filters
   * clues whose plant/payoff (or narrative chapter) intersects [start, end].
   */
  chapterStart?: number | null;
  chapterEnd?: number | null;
};

function resolveClueSource(
  envelope: ClueEnvelope,
  preferred: "active" | "running_candidate",
  runStatus: string | null | undefined
): "active" | "running_candidate" {
  const live = Boolean(runStatus && CLUE_ACTIVE_RUN.has(runStatus));
  if (live && envelope.running_candidate) return "running_candidate";
  if (!live && envelope.active) return "active";
  if (envelope[preferred]) return preferred;
  if (envelope.active) return "active";
  if (envelope.running_candidate) return "running_candidate";
  return preferred;
}

const RUN_LABELS: Record<string, string> = {
  empty: "尚未分析线索",
  pending: "等待开始",
  running: "正在分析线索",
  partial: "已有部分线索",
  paused_budget: "预算不足，已暂停",
  paused_dependency: "依赖未就绪（通常需先完成时间线）",
  cancelled: "已暂停",
  failed: "线索分析失败",
  completed: "线索分析完成",
};

export function ClueWorkspace(props: Props) {
  const [envelope, setEnvelope] = useState<ClueEnvelope>({
    active: null,
    running_candidate: null,
  });
  const [source, setSource] = useState<"active" | "running_candidate">("active");
  const [run, setRun] = useState<ClueRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState<ClueState | "">("");
  const [characterId, setCharacterId] = useState<number | "">("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ClueDetailPanels | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [listExpanded, setListExpanded] = useState(true);
  const [actionBusyNote, setActionBusyNote] = useState("");

  const requestIdRef = useRef(0);
  const detailRequestIdRef = useRef(0);
  const sourceRef = useRef(source);
  const runStatusRef = useRef<string | null>(null);
  const currentRunStatus = run?.status ?? null;

  useEffect(() => {
    sourceRef.current = source;
  }, [source]);

  useEffect(() => {
    runStatusRef.current = currentRunStatus;
  }, [currentRunStatus]);

  const loadEnvelope = useCallback(async () => {
    if (!props.novelId) return;
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError("");
    try {
      const response = await clueApi.getClues(props.novelId, {
        full_book: props.fullBook,
        character_id: characterId === "" ? undefined : characterId,
        status: statusFilter || undefined,
      });
      if (requestId !== requestIdRef.current) return;
      const data = response.data;
      setEnvelope(data);
      const nextSource = resolveClueSource(
        data,
        sourceRef.current,
        runStatusRef.current
      );
      if (nextSource !== sourceRef.current) {
        setSource(nextSource);
        sourceRef.current = nextSource;
      }
      setSelectedId((prev) => {
        if (!prev) return null;
        const view = data[nextSource];
        return view?.clues.some((c) => c.logical_clue_id === prev) ? prev : null;
      });
    } catch {
      if (requestId !== requestIdRef.current) return;
      setEnvelope({ active: null, running_candidate: null });
      setError("加载线索失败。");
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [props.novelId, props.fullBook, characterId, statusFilter]);

  const loadStatus = useCallback(async () => {
    if (!props.novelId) return;
    try {
      const res = await clueApi.status(props.novelId);
      setRun(res.data);
      runStatusRef.current = res.data.status;
    } catch {
      setRun(null);
      runStatusRef.current = null;
    }
  }, [props.novelId]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      void loadStatus();
      void loadEnvelope();
    });
    return () => {
      cancelled = true;
    };
  }, [loadStatus, loadEnvelope]);

  // Poll while clue run is live
  useEffect(() => {
    if (!props.novelId || !currentRunStatus || !CLUE_ACTIVE_RUN.has(currentRunStatus)) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const statusRes = await clueApi.status(props.novelId);
        if (cancelled) return;
        setRun(statusRes.data);
        runStatusRef.current = statusRes.data.status;
        await loadEnvelope();
      } catch {
        /* soft */
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [props.novelId, currentRunStatus, loadEnvelope]);

  const view = envelope[source];
  const orderedClues = useMemo(() => {
    const sorted = sortVisibleClues(view?.clues ?? []);
    const start = props.chapterStart;
    const end = props.chapterEnd;
    if (
      start == null ||
      end == null ||
      !Number.isFinite(start) ||
      !Number.isFinite(end) ||
      start < 1 ||
      end < start
    ) {
      return sorted;
    }
    return sorted.filter((c) => clueIntersectsChapterRange(c, start, end));
  }, [view, props.chapterStart, props.chapterEnd]);

  const selectedClue: VisibleClue | null = useMemo(() => {
    if (!selectedId) return null;
    return orderedClues.find((c) => c.logical_clue_id === selectedId) ?? null;
  }, [orderedClues, selectedId]);

  // Load detail when selection or version/full_book changes
  useEffect(() => {
    let cancelled = false;
    const requestId = ++detailRequestIdRef.current;
    const clue = selectedClue;
    const versionId = view?.version_id;

    queueMicrotask(() => {
      if (cancelled || requestId !== detailRequestIdRef.current) return;
      if (!clue || !versionId || !props.novelId) {
        setDetail(null);
        setDetailLoading(false);
        setDetailError("");
        return;
      }
      setDetailLoading(true);
      setDetailError("");
      clueApi
        .getDetail(props.novelId, versionId, clue.logical_clue_id, {
          full_book: props.fullBook,
        })
        .then((res) => {
          if (cancelled || requestId !== detailRequestIdRef.current) return;
          setDetail(res.data);
        })
        .catch(() => {
          if (cancelled || requestId !== detailRequestIdRef.current) return;
          setDetail(null);
          setDetailError("证据不可见或不存在。");
        })
        .finally(() => {
          if (cancelled || requestId !== detailRequestIdRef.current) return;
          setDetailLoading(false);
        });
    });

    return () => {
      cancelled = true;
    };
  }, [selectedClue, view?.version_id, props.novelId, props.fullBook]);

  async function startOrResume() {
    if (!props.novelId) return;
    setActionBusyNote("正在启动线索分析…");
    setError("");
    try {
      await clueApi.startOrResume(props.novelId);
      const statusRes = await clueApi.status(props.novelId);
      setRun(statusRes.data);
      runStatusRef.current = statusRes.data.status;
      if (CLUE_ACTIVE_RUN.has(statusRes.data.status)) {
        setSource("running_candidate");
        sourceRef.current = "running_candidate";
      }
      await loadEnvelope();
      setActionBusyNote("");
    } catch (err: unknown) {
      const detailMsg =
        err &&
        typeof err === "object" &&
        "response" in err &&
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail;
      setError(
        typeof detailMsg === "string" ? detailMsg : "启动线索分析失败。"
      );
      setActionBusyNote("");
    }
  }

  async function pauseRun() {
    if (!props.novelId) return;
    try {
      const res = await clueApi.cancel(props.novelId);
      setRun(res.data);
    } catch {
      setError("暂停失败。");
    }
  }

  async function runAction(
    logicalId: string,
    payload: Parameters<typeof clueApi.action>[2]
  ) {
    setActionBusy(true);
    setActionError("");
    try {
      await clueApi.action(props.novelId, logicalId, payload);
      // Authority refresh — no optimistic state fabrication
      await loadEnvelope();
      if (view?.version_id) {
        const res = await clueApi.getDetail(
          props.novelId,
          view.version_id,
          logicalId,
          { full_book: props.fullBook }
        );
        setDetail(res.data);
      }
    } catch (err: unknown) {
      const detailMsg =
        err &&
        typeof err === "object" &&
        "response" in err &&
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail;
      setActionError(
        typeof detailMsg === "string" ? detailMsg : "动作提交失败。"
      );
    } finally {
      setActionBusy(false);
    }
  }

  const runStatus = run?.status ?? (orderedClues.length ? "completed" : "empty");
  const canStart =
    runStatus === "empty" || !run || runStatus === "completed";
  const canResume = [
    "cancelled",
    "paused_budget",
    "paused_dependency",
    "failed",
  ].includes(String(runStatus));

  return (
    <div className="grid gap-3" data-testid="clue-workspace">
      {/* Clue run status (independent of timeline worker) */}
      <div
        className="grid gap-2 rounded-2xl border bg-card p-4"
        role="status"
        aria-live="polite"
        data-testid="clue-run-status"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-semibold">
            {RUN_LABELS[runStatus] ?? runStatus}
          </span>
          <div className="flex flex-wrap gap-2">
            {canStart && (
              <button
                type="button"
                className="rounded-xl bg-foreground px-4 py-2 text-sm text-background disabled:opacity-50"
                disabled={Boolean(actionBusyNote)}
                onClick={() => void startOrResume()}
              >
                开始线索分析
              </button>
            )}
            {canResume && (
              <button
                type="button"
                className="rounded-xl bg-foreground px-4 py-2 text-sm text-background disabled:opacity-50"
                disabled={Boolean(actionBusyNote)}
                onClick={() => void startOrResume()}
              >
                继续分析
              </button>
            )}
            {run && CLUE_ACTIVE_RUN.has(run.status) && (
              <button
                type="button"
                className="rounded-xl border px-4 py-2 text-sm"
                onClick={() => void pauseRun()}
              >
                暂停
              </button>
            )}
          </div>
        </div>
        {actionBusyNote && (
          <p className="text-xs text-muted-foreground">{actionBusyNote}</p>
        )}
      </div>

      {/* Shared full-book control (parent owns persistence) */}
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border bg-card/60 p-3">
        <label className="flex h-10 items-center gap-2 rounded-xl border border-amber-300/70 bg-amber-50 px-3 text-sm text-amber-950">
          <input
            type="checkbox"
            aria-label="显示全书（可能剧透）"
            checked={props.fullBook}
            onChange={(event) => props.onFullBookRequest(event.target.checked)}
          />
          显示全书（可能剧透）
        </label>
        <p className="text-xs text-muted-foreground">
          与时间线共用 Phase 08 全书偏好；线索无独立剧透开关。
        </p>
      </div>

      <ClueControls
        envelope={envelope}
        source={source as ClueVersionSource}
        onSourceChange={(next) => {
          setSource(next);
          sourceRef.current = next;
          setStatusFilter("");
          setCharacterId("");
        }}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        characterId={characterId}
        onCharacterIdChange={setCharacterId}
        availableStates={view?.available_states ?? []}
        availableCharacterIds={view?.available_character_ids ?? []}
        counts={view?.counts ?? null}
      />

      {error && (
        <p
          role="alert"
          className="rounded-xl bg-destructive/10 p-3 text-sm text-destructive"
        >
          {error}
        </p>
      )}

      {loading && !view ? (
        <div
          className="grid h-64 min-h-64 place-items-center rounded-3xl bg-muted motion-transition-content"
          role="status"
          aria-busy="true"
          aria-label="正在加载线索"
          data-testid="clue-loading"
        >
          <p className="text-sm text-muted-foreground">正在加载线索…</p>
        </div>
      ) : view ? (
        <ClueBand
          clues={orderedClues}
          selectedId={selectedId}
          onSelect={setSelectedId}
          payoffChain={
            selectedClue && detail?.clue.logical_clue_id === selectedClue.logical_clue_id
              ? detail.payoff_chain
              : null
          }
          listExpanded={listExpanded}
          onToggleList={() => setListExpanded((v) => !v)}
        />
      ) : (
        <div className="grid min-h-48 place-items-center rounded-3xl border border-dashed p-8 text-center text-muted-foreground">
          <div>
            <p className="text-sm">暂无线索结果。</p>
            <p className="mt-2 text-xs">
              点上方「开始线索分析」后，可见线索会以埋设→兑现卡片列出。
            </p>
          </div>
        </div>
      )}

      {selectedClue && (
        <ClueEvidencePanel
          novelId={props.novelId}
          clue={selectedClue}
          detail={detail}
          loading={detailLoading}
          error={detailError}
          actionBusy={actionBusy}
          actionError={actionError}
          onClose={() => {
            setSelectedId(null);
            setDetail(null);
            setActionError("");
          }}
          onConfirm={(reason) =>
            void runAction(selectedClue.logical_clue_id, {
              action: "confirm",
              reason,
            })
          }
          onReject={(reason) =>
            void runAction(selectedClue.logical_clue_id, {
              action: "reject",
              reason,
            })
          }
          onAnnotate={(reason, note) =>
            void runAction(selectedClue.logical_clue_id, {
              action: "annotate",
              reason,
              note,
            })
          }
          onAdjustLink={(reason, link) => {
            const body: {
              target_kind: ClueLinkTargetKind;
              character_id?: number;
              timeline_event_id?: number;
              relationship_observation_ref?: string;
            } = { target_kind: link.target_kind };
            if (link.character_id != null) body.character_id = link.character_id;
            if (link.timeline_event_id != null) {
              body.timeline_event_id = link.timeline_event_id;
            }
            if (link.relationship_observation_ref) {
              body.relationship_observation_ref =
                link.relationship_observation_ref;
            }
            void runAction(selectedClue.logical_clue_id, {
              action: "adjust_link",
              reason,
              link: body,
            });
          }}
        />
      )}
    </div>
  );
}
