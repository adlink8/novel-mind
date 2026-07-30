"use client";

import { useRef, useState, type ReactNode, type RefObject } from "react";
import Link from "next/link";
import { X } from "lucide-react";

import {
  CLUE_STATE_LABELS,
  type ClueDetailPanels,
  type ClueEvidenceItem,
  type ClueLinkTargetKind,
  type ClueState,
  type VisibleClue,
} from "@/lib/clue-api";
import { useDismissableLayer } from "@/lib/use-dismissable-layer";
import { cn } from "@/lib/utils";

const ROLE_LABELS: Record<string, string> = {
  cue: "预告",
  reinforcement: "强化",
  payoff: "回收",
  disposition: "处置",
};

/** Stable display order for evidence roles; unknown roles append last. */
const ROLE_ORDER = ["cue", "reinforcement", "payoff", "disposition"] as const;

function groupEvidenceByRole(
  evidence: ClueEvidenceItem[]
): Array<{ role: string; items: ClueEvidenceItem[] }> {
  const buckets = new Map<string, ClueEvidenceItem[]>();
  for (const item of evidence) {
    const role = String(item.role || "cue");
    const list = buckets.get(role);
    if (list) list.push(item);
    else buckets.set(role, [item]);
  }
  const ordered: Array<{ role: string; items: ClueEvidenceItem[] }> = [];
  for (const role of ROLE_ORDER) {
    const items = buckets.get(role);
    if (items?.length) ordered.push({ role, items });
    buckets.delete(role);
  }
  for (const [role, items] of buckets) {
    if (items.length) ordered.push({ role, items });
  }
  return ordered;
}

const LINK_KIND_LABELS: Record<string, string> = {
  character: "人物",
  timeline_event: "时间事件",
  relationship_observation: "关系观察",
};

type Props = {
  novelId: string;
  clue: VisibleClue | null;
  detail: ClueDetailPanels | null;
  loading?: boolean;
  error?: string;
  actionBusy?: boolean;
  actionError?: string;
  onClose: () => void;
  onConfirm: (reason: string) => void;
  onReject: (reason: string) => void;
  onAnnotate: (reason: string, note: string) => void;
  onAdjustLink: (
    reason: string,
    link: {
      target_kind: ClueLinkTargetKind;
      character_id?: number;
      timeline_event_id?: number;
      relationship_observation_ref?: string;
    }
  ) => void;
};

function NestedConfirmLayer({
  open,
  onDismiss,
  label,
  className,
  children,
}: {
  open: boolean;
  onDismiss: () => void;
  label: string;
  className?: string;
  children: ReactNode;
}) {
  const layerRef = useRef<HTMLDivElement>(null);
  const { present, closing } = useDismissableLayer({
    open,
    onDismiss,
    layerRef: layerRef as RefObject<HTMLElement | null>,
    // Nested confirmation is topmost; outside/Escape closes it before parent.
    closeOnOutside: true,
  });
  if (!present) return null;
  return (
    <div
      ref={layerRef}
      role="alertdialog"
      aria-label={label}
      aria-hidden={closing || undefined}
      className={cn(
        className,
        closing && "pointer-events-none opacity-60"
      )}
    >
      {children}
    </div>
  );
}

export function ClueEvidencePanel(props: Props) {
  const { clue, detail } = props;
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [confirmReject, setConfirmReject] = useState(false);
  const [confirmLink, setConfirmLink] = useState(false);
  const [linkKind, setLinkKind] = useState<ClueLinkTargetKind>("character");
  const [linkTarget, setLinkTarget] = useState("");
  const layerRef = useRef<HTMLElement>(null);

  const open = clue != null;
  const { present, closing } = useDismissableLayer({
    open,
    onDismiss: () => {
      resetForms();
      props.onClose();
    },
    layerRef,
    closeOnOutside: false, // backdrop owns outside
  });

  function resetForms() {
    setReason("");
    setNote("");
    setConfirmReject(false);
    setConfirmLink(false);
    setLinkTarget("");
  }

  if (!clue || !present) return null;

  const state = clue.derived_state;
  const needsRelink = detail?.links.some(
    (l) => l.validation_status === "source_unavailable" || l.validation_status === "unresolved"
  );

  return (
    <>
      <button
        type="button"
        aria-label="关闭证据遮罩"
        className={cn(
          "fixed inset-0 z-40 bg-black/30 transition-[opacity] motion-duration-spatial motion-ease-enter",
          open && !closing ? "opacity-100" : "pointer-events-none opacity-0 motion-ease-exit"
        )}
        onClick={() => {
          resetForms();
          props.onClose();
        }}
      />
      <aside
        ref={layerRef}
        role="dialog"
        aria-modal="true"
        aria-label="线索证据"
        aria-hidden={closing || undefined}
        data-testid="clue-evidence-panel"
        className={cn(
          "fixed bottom-0 right-0 top-0 z-50 flex w-full max-w-md flex-col border-l bg-background shadow-2xl transition-[opacity,transform] motion-duration-spatial motion-ease-enter",
          open && !closing
            ? "translate-x-0 opacity-100"
            : "pointer-events-none translate-x-6 opacity-0 motion-ease-exit"
        )}
      >
        <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wider text-primary">
              {CLUE_STATE_LABELS[state] ?? state}
              {clue.provenance.state === "manual" ? " · 人工" : " · 机器"}
            </p>
            <h2 className="mt-1 font-serif text-xl font-semibold leading-snug">
              {clue.title}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              第{clue.narrative_chapter_number}章 · 偏移 {clue.source_start} ·
              置信度 {Math.round(clue.confidence * 100)}%
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭线索证据"
            className="shrink-0 rounded-full border p-2"
            onClick={() => {
              resetForms();
              props.onClose();
            }}
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {needsRelink && (
            <p
              role="status"
              className="mb-3 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-950"
              data-testid="clue-needs-relink"
            >
              关联待核对（needs_relink / unresolved / source_unavailable）
            </p>
          )}

          <p className="text-xs font-medium text-muted-foreground">版本溯源</p>
          <ul className="mt-1 text-sm">
            {Object.entries(clue.provenance).map(([field, kind]) => (
              <li key={field}>
                {field}: {kind === "manual" ? "人工" : "机器"}
              </li>
            ))}
            {Object.keys(clue.provenance).length === 0 && (
              <li className="text-muted-foreground">默认机器推导</li>
            )}
          </ul>

          {/* Payoff chain from server detail */}
          {detail && detail.payoff_chain.length > 0 && (
            <div className="mt-4" data-testid="panel-payoff-chain">
              <p className="text-xs font-medium text-muted-foreground">
                回收链
              </p>
              <ol className="mt-2 flex flex-wrap items-center gap-2">
                {detail.payoff_chain.map((step, i) => (
                  <li
                    key={`${step.event_key}-${i}`}
                    className="flex items-center gap-2 text-sm"
                  >
                    {i > 0 && <span aria-hidden>→</span>}
                    <span className="rounded-full border px-2 py-0.5 text-xs">
                      {CLUE_STATE_LABELS[step.to_status as ClueState] ??
                        step.to_status}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          <p className="mt-4 text-xs font-medium text-muted-foreground">
            证据（按角色分组：cue → reinforcement → payoff）
          </p>
          {props.loading && (
            <p className="mt-2 text-sm text-muted-foreground">加载证据…</p>
          )}
          {props.error && (
            <p role="alert" className="mt-2 text-sm text-destructive">
              {props.error}
            </p>
          )}
          {!props.loading && !props.error && detail && (
            <div className="mt-2 grid gap-3" data-testid="clue-evidence-by-role">
              {groupEvidenceByRole(detail.evidence).map(({ role, items }) => (
                <div key={role} data-testid={`clue-evidence-role-${role}`}>
                  <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {ROLE_LABELS[role] ?? role}
                    <span className="ml-1 font-normal normal-case">
                      ({items.length})
                    </span>
                  </p>
                  <ul className="grid gap-2">
                    {items.map((item) => (
                      <li
                        key={`${item.evidence_id}-${item.chapter_id}-${item.source_start}-${item.source_end}`}
                        className="rounded-2xl border bg-card p-3 text-sm"
                      >
                        <p className="text-xs text-muted-foreground">
                          第{item.narrative_chapter_number}章 · 偏移{" "}
                          {item.source_start}–{item.source_end}
                        </p>
                        {item.excerpt && (
                          <p className="mt-1 leading-6 text-muted-foreground">
                            {item.excerpt}
                          </p>
                        )}
                        <Link
                          href={`/novels/${props.novelId}?chapter=${item.chapter_id}&start=${item.source_start}`}
                          className="mt-2 inline-block text-xs text-primary underline-offset-2 hover:underline"
                        >
                          跳转原文章节
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              {detail.evidence.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  当前可见范围内无证据。
                </p>
              )}
            </div>
          )}

          <p className="mt-4 text-xs font-medium text-muted-foreground">
            关联
          </p>
          {!props.loading && detail && (
            <ul className="mt-2 grid gap-2">
              {detail.links.map((link, i) => (
                <li
                  key={i}
                  className="rounded-xl border px-3 py-2 text-sm"
                >
                  {LINK_KIND_LABELS[link.target_kind] ?? link.target_kind}
                  {link.character_id != null && ` #${link.character_id}`}
                  {link.timeline_event_id != null &&
                    ` 事件#${link.timeline_event_id}`}
                  {link.relationship_observation_ref &&
                    ` ${link.relationship_observation_ref}`}
                  <span className="ml-2 text-xs text-muted-foreground">
                    {link.validation_status}
                  </span>
                </li>
              ))}
              {detail.links.length === 0 && (
                <li className="text-sm text-muted-foreground">无关联</li>
              )}
            </ul>
          )}

          {/* Protected human actions */}
          <div className="mt-6 grid gap-3 border-t pt-4">
            <p className="text-xs font-medium text-muted-foreground">
              人工动作（写回后从服务端刷新，不做乐观事实）
            </p>
            <label className="grid gap-1 text-xs text-muted-foreground">
              原因（必填）
              <input
                aria-label="动作原因"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="h-10 rounded-xl border bg-background px-3 text-sm text-foreground"
                placeholder="说明判断依据"
              />
            </label>

            {props.actionError && (
              <p role="alert" className="text-sm text-destructive">
                {props.actionError}
              </p>
            )}

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={props.actionBusy || !reason.trim()}
                className="rounded-xl bg-foreground px-3 py-2 text-xs text-background disabled:opacity-50"
                onClick={() => props.onConfirm(reason.trim())}
              >
                确认
              </button>
              <button
                type="button"
                disabled={props.actionBusy || !reason.trim()}
                className="rounded-xl border border-destructive/50 px-3 py-2 text-xs text-destructive disabled:opacity-50"
                onClick={() => setConfirmReject(true)}
              >
                驳回
              </button>
            </div>

            <NestedConfirmLayer
              open={confirmReject}
              onDismiss={() => setConfirmReject(false)}
              label="确认驳回"
              className="rounded-xl border border-destructive/40 bg-destructive/5 p-3 text-sm"
            >
              <p>确认驳回该线索？此操作会追加人工 override，不可静默撤销。</p>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  className="rounded-lg border px-3 py-1.5 text-xs"
                  onClick={() => setConfirmReject(false)}
                >
                  取消
                </button>
                <button
                  type="button"
                  disabled={props.actionBusy}
                  className="rounded-lg bg-destructive px-3 py-1.5 text-xs text-white disabled:opacity-50"
                  onClick={() => {
                    props.onReject(reason.trim());
                    setConfirmReject(false);
                  }}
                >
                  确认驳回
                </button>
              </div>
            </NestedConfirmLayer>

            <label className="grid gap-1 text-xs text-muted-foreground">
              注释
              <textarea
                aria-label="注释内容"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={2}
                className="rounded-xl border bg-background px-3 py-2 text-sm text-foreground"
                placeholder="可选备注"
              />
            </label>
            <button
              type="button"
              disabled={props.actionBusy || !reason.trim() || !note.trim()}
              className="w-fit rounded-xl border px-3 py-2 text-xs disabled:opacity-50"
              onClick={() => props.onAnnotate(reason.trim(), note.trim())}
            >
              保存注释
            </button>

            <div className="grid gap-2 rounded-xl border p-3">
              <p className="text-xs font-medium">调整关联</p>
              <label className="grid gap-1 text-xs text-muted-foreground">
                目标类型
                <select
                  aria-label="关联目标类型"
                  value={linkKind}
                  onChange={(e) =>
                    setLinkKind(e.target.value as ClueLinkTargetKind)
                  }
                  className="h-9 rounded-lg border bg-background px-2 text-sm"
                >
                  <option value="character">人物</option>
                  <option value="timeline_event">时间事件</option>
                  <option value="relationship_observation">关系观察</option>
                </select>
              </label>
              <label className="grid gap-1 text-xs text-muted-foreground">
                目标 ID / 引用
                <input
                  aria-label="关联目标值"
                  value={linkTarget}
                  onChange={(e) => setLinkTarget(e.target.value)}
                  className="h-9 rounded-lg border bg-background px-2 text-sm"
                  placeholder={
                    linkKind === "relationship_observation"
                      ? "observation ref"
                      : "数字 ID"
                  }
                />
              </label>
              <button
                type="button"
                disabled={
                  props.actionBusy || !reason.trim() || !linkTarget.trim()
                }
                className="w-fit rounded-xl border px-3 py-2 text-xs disabled:opacity-50"
                onClick={() => setConfirmLink(true)}
              >
                提交关联调整
              </button>
              <NestedConfirmLayer
                open={confirmLink}
                onDismiss={() => setConfirmLink(false)}
                label="确认关联调整"
                className="rounded-lg border border-amber-300 bg-amber-50 p-2 text-xs text-amber-950"
              >
                <p>确认替换/写入该关联？需显式确认后才会提交。</p>
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    className="rounded border px-2 py-1"
                    onClick={() => setConfirmLink(false)}
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    disabled={props.actionBusy}
                    className="rounded bg-foreground px-2 py-1 text-background disabled:opacity-50"
                    onClick={() => {
                      const base = {
                        target_kind: linkKind,
                      } as {
                        target_kind: ClueLinkTargetKind;
                        character_id?: number;
                        timeline_event_id?: number;
                        relationship_observation_ref?: string;
                      };
                      if (linkKind === "character") {
                        base.character_id = Number(linkTarget);
                      } else if (linkKind === "timeline_event") {
                        base.timeline_event_id = Number(linkTarget);
                      } else {
                        base.relationship_observation_ref = linkTarget.trim();
                      }
                      props.onAdjustLink(reason.trim(), base);
                      setConfirmLink(false);
                    }}
                  >
                    确认提交
                  </button>
                </div>
              </NestedConfirmLayer>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
