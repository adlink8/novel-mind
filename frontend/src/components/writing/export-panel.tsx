"use client";

/**
 * Phase 39-03 derivative export review + download panel (D-39-03,
 * T-39-03-01/02).
 *
 * The browser can request an export **only** from an approved
 * `ExportPreparationArtifact`:
 *
 * - the panel discovers the approved `approve_export` ApprovalRequest and the
 *   ExportPreparationArtifact it is bound to (owner-scoped read surfaces);
 * - it renders preparation_id / revision / export version / manifest checksum,
 *   the server-frozen approved asset/citation counts and the three-dimension
 *   audit (implementation_readiness / sample_data_coverage /
 *   quality_qualification) with blocked reasons;
 * - the export button submits only the deterministic `agent/materialize`
 *   request of the approved artifact — the browser never assembles a manifest
 *   or selects a live revision;
 * - after download the panel verifies `X-Export-Manifest-Hash` against the
 *   artifact's frozen manifest checksum; a completed download is NEVER shown
 *   as a quality pass (quality always comes from the audit report, and EPUB
 *   interoperability is explicitly unverified without a validator);
 * - cross-owner / Original / pending / rejected / stale / forged / missing
 *   asset outcomes surface as comprehensible, fail-closed errors with a retry
 *   entry point. No innerHTML; every control has an accessible label.
 */

import { useCallback, useEffect, useState } from "react";

import { Loader2, RefreshCw } from "lucide-react";

import type {
  DerivativeExportAuditDimension,
  DerivativeExportAuditReport,
  DerivativeExportFormat,
} from "@/lib/derivative-export-api";
import { derivativeExportApi } from "@/lib/derivative-export-api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Display vocabulary (mirrors the backend closed audit status enums)
// ---------------------------------------------------------------------------

export const AUDIT_STATUS_LABEL_TEXT: Record<string, string> = {
  verified: "已验证",
  partial: "部分通过",
  blocked: "已阻断",
};

export const AUDIT_STATUS_BADGE_CLASS: Record<string, string> = {
  verified: "border-emerald-500/40 bg-emerald-500/10 text-emerald-800",
  partial: "border-amber-500/40 bg-amber-500/10 text-amber-800",
  blocked: "border-rose-600/50 bg-rose-600/10 text-rose-700",
};

export const AUDIT_DIMENSION_LABEL_TEXT: Record<string, string> = {
  implementation_readiness: "实现就绪",
  sample_data_coverage: "样例数据覆盖",
  quality_qualification: "质量资格",
};

export function shortExportHash(value: string | null | undefined): string {
  if (!value || value.length <= 16) return value ?? "";
  return `${value.slice(0, 8)}…${value.slice(-4)}`;
}

export type ExportPanelProps = {
  novelId: number | string;
  projectId: number;
  className?: string;
};

type LoadState =
  | { kind: "loading" }
  | { kind: "empty"; reason: string }
  | { kind: "blocked"; reason: string; detail?: string }
  | { kind: "error"; message: string }
  | { kind: "ready"; view: ExportView };

interface ExportView {
  artifactId: number;
  artifactRevisionId: number;
  approvalId: number;
  preparationHash: string;
  /** Frozen manifest checksum of the approved artifact. */
  manifestChecksum: string;
  snapshotHash: string;
  fork: string;
  branch: string | null;
  exportVersion: string;
  projectKey: string;
  counts: {
    chapters: number;
    assets: number;
    revisions: number;
    citations: number;
  };
  audit: DerivativeExportAuditReport;
}

type FormatStatus = "idle" | "working" | "done" | "error";

interface FormatState {
  status: FormatStatus;
  error?: string;
}

const IDLE_FORMATS: Record<DerivativeExportFormat, FormatState> = {
  markdown: { status: "idle" },
  epub: { status: "idle" },
};

const FORMAT_LABEL_TEXT: Record<DerivativeExportFormat, string> = {
  markdown: "Markdown",
  epub: "EPUB",
};

// ---------------------------------------------------------------------------
// Small helpers (no unsafe rendering, no innerHTML)
// ---------------------------------------------------------------------------

function extractError(err: unknown): string {
  if (err && typeof err === "object" && "response" in err) {
    const data = (err as { response?: { data?: unknown } }).response?.data;
    if (data && typeof data === "object" && "detail" in data) {
      const detail = (data as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) return detail;
    }
  }
  return err instanceof Error ? err.message : "未知错误";
}

function triggerFileDownload(blob: Blob, filename: string) {
  if (
    typeof URL === "undefined" ||
    typeof URL.createObjectURL !== "function"
  ) {
    // jsdom / non-browser environments: the header verification already ran.
    return;
  }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function exportFilename(format: DerivativeExportFormat, view: ExportView): string {
  const stem =
    (view.projectKey || "").replace(/[^\w\-]+/g, "-").replace(/^-+|-+$/g, "") ||
    "derivative";
  const ext = format === "markdown" ? "md" : "epub";
  return `${stem}-v${view.snapshotHash.slice(0, 8)}.${ext}`;
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export function ExportPanel({ novelId, projectId, className }: ExportPanelProps) {
  const [loadState, setLoadState] = useState<LoadState>({ kind: "loading" });
  const [formats, setFormats] = useState<Record<DerivativeExportFormat, FormatState>>(
    IDLE_FORMATS
  );

  const load = useCallback(async () => {
    setLoadState({ kind: "loading" });
    try {
      const approvalRes = await derivativeExportApi.listApprovalRequests();
      const approvals = approvalRes.data.items
        .filter((row) => row.action === "approve_export")
        .filter((row) => row.status === "approved")
        .filter(
          (row) => row.payload_summary?.project_id === Number(projectId)
        )
        .sort((a, b) => b.id - a.id);

      if (approvals.length === 0) {
        setLoadState({
          kind: "empty",
          reason: "当前项目没有已批准的导出准备 artifact（approve_export approval 不存在或尚未批准）",
        });
        return;
      }

      let ready: ExportView | null = null;
      let lastReason: string | null = null;
      let stale = false;
      for (const approval of approvals) {
        const summary = approval.payload_summary ?? {};
        const artifactId = summary.artifact_id;
        if (!artifactId) continue;
        try {
          const artifact = (await derivativeExportApi.getArtifact(novelId, artifactId)).data;
          if (artifact.type !== "export_preparation") continue;
          if (artifact.status !== "approved") continue;
          const revisionId =
            artifact.current_revision_id ?? summary.artifact_revision_id;
          if (!revisionId) continue;
          const revisions = (
            await derivativeExportApi.listArtifactRevisions(novelId, artifactId)
          ).data.items;
          const revision = revisions.find((row) => row.id === revisionId);
          const preparation = revision?.content?.preparation as
            | { content_hash: string; fork: string; project_key: string; evidence_refs?: unknown }
            | undefined;
          if (!preparation?.content_hash) {
            lastReason = `artifact ${artifactId} 内容缺少冻结的 preparation 载荷`;
            continue;
          }

          const [auditRes, prepareRes] = await Promise.all([
            derivativeExportApi.audit(novelId, projectId),
            derivativeExportApi.agentPrepare(novelId, projectId, {
              branch: summary.branch ?? artifact.branch ?? null,
              fork: preparation.fork,
              evidence_refs: Array.isArray(preparation.evidence_refs)
                ? preparation.evidence_refs
                : [],
            }),
          ]);
          const staleNow =
            prepareRes.data.manifest_hash !== preparation.content_hash;
          if (staleNow) {
            stale = true;
            lastReason = `服务端当前冻结快照与已批准 artifact 的 manifest checksum 不一致（已批准 ${shortExportHash(preparation.content_hash)} ≠ 当前服务端 ${shortExportHash(prepareRes.data.manifest_hash)}）`;
            break;
          }
          ready = {
            artifactId: artifact.id,
            artifactRevisionId: revisionId,
            approvalId: approval.id,
            preparationHash: prepareRes.data.preparation_hash,
            manifestChecksum: preparation.content_hash,
            snapshotHash: prepareRes.data.snapshot_hash,
            fork: preparation.fork,
            branch: summary.branch ?? artifact.branch ?? null,
            exportVersion: prepareRes.data.export_version,
            projectKey: preparation.project_key,
            counts: {
              chapters: prepareRes.data.chapter_count,
              assets: prepareRes.data.asset_count,
              revisions: prepareRes.data.revision_count,
              citations: prepareRes.data.citation_count,
            },
            audit: auditRes.data,
          };
          break;
        } catch {
          lastReason = `approved artifact ${artifactId} 读取失败`;
          continue;
        }
      }

      if (stale) {
        setLoadState({
          kind: "blocked",
          reason: "artifact 已过期：服务端当前冻结快照与已批准 artifact 的 manifest checksum 不一致，需重新批准后才能导出",
          detail: lastReason ?? undefined,
        });
        return;
      }
      if (!ready) {
        setLoadState({
          kind: "blocked",
          reason: "已批准 approval 未对应到有效的已批准 ExportPreparationArtifact",
          detail: lastReason ?? undefined,
        });
        return;
      }
      setLoadState({ kind: "ready", view: ready });
      setFormats(IDLE_FORMATS);
    } catch {
      setLoadState({ kind: "error", message: "导出状态加载失败，请稍后重试" });
    }
  }, [novelId, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleExport = useCallback(
    async (format: DerivativeExportFormat) => {
      if (loadState.kind !== "ready") return;
      const view = loadState.view;
      setFormats((current) => ({ ...current, [format]: { status: "working" } }));
      try {
        const materialized = (
          await derivativeExportApi.materialize(novelId, projectId, {
            branch: view.branch,
            fork: view.fork,
            artifact_id: view.artifactId,
            artifact_revision_id: view.artifactRevisionId,
            approval_id: view.approvalId,
            preparation_hash: view.preparationHash,
            reason: "browser derivative export UAT",
          })
        ).data;
        if (materialized.manifest_hash !== view.manifestChecksum) {
          throw new Error(
            `物化返回的 manifest hash 与已批准 artifact 不一致（${shortExportHash(materialized.manifest_hash)} ≠ ${shortExportHash(view.manifestChecksum)}）`
          );
        }

        const downloadRes = await derivativeExportApi.download(
          novelId,
          projectId,
          format
        );
        const headerHash = downloadRes.headers["x-export-manifest-hash"];
        if (headerHash !== view.manifestChecksum) {
          throw new Error(
            `下载文件 manifest 头校验失败（${shortExportHash(headerHash ?? "")} ≠ ${shortExportHash(view.manifestChecksum)}），请重试`
          );
        }
        triggerFileDownload(downloadRes.data, exportFilename(format, view));
        setFormats((current) => ({ ...current, [format]: { status: "done" } }));
      } catch (err) {
        setFormats((current) => ({
          ...current,
          [format]: { status: "error", error: extractError(err) },
        }));
      }
    },
    [loadState, novelId, projectId]
  );

  const handleReload = useCallback(() => {
    setFormats(IDLE_FORMATS);
    void load();
  }, [load]);

  return (
    <section
      data-testid="derivative-export-panel"
      aria-label="Derivative 导出与质量审查"
      className={cn("space-y-4", className)}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-serif text-lg font-semibold">导出与质量审查</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            只能从已批准的导出准备 artifact 请求导出；下载完成不代表质量通过。
          </p>
        </div>
        <button
          type="button"
          data-testid="derivative-export-reload"
          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-semibold"
          onClick={() => void handleReload()}
          disabled={loadState.kind === "loading"}
        >
          <RefreshCw className="size-3.5" />
          重新加载
        </button>
      </div>

      {loadState.kind === "error" ? (
        <div
          role="alert"
          data-testid="derivative-export-error"
          className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-800"
        >
          {loadState.message}
        </div>
      ) : null}

      {loadState.kind === "loading" ? (
        <p
          data-testid="derivative-export-loading"
          className="flex items-center gap-2 text-xs text-muted-foreground"
        >
          <Loader2 className="size-3.5 animate-spin" />
          正在加载已批准的导出准备…
        </p>
      ) : null}

      {loadState.kind === "empty" ? (
        <p
          data-testid="derivative-export-empty"
          className="rounded-xl border border-dashed border-border p-6 text-center text-xs text-muted-foreground"
        >
          {loadState.reason}
        </p>
      ) : null}

      {loadState.kind === "blocked" ? (
        <div
          data-testid="derivative-export-blocked"
          className="space-y-2 rounded-xl border border-rose-500/40 bg-rose-500/5 p-4"
        >
          <p className="text-xs font-semibold text-rose-800">{loadState.reason}</p>
          {loadState.detail ? (
            <p className="font-mono text-[10px] leading-4 text-rose-700">
              {loadState.detail}
            </p>
          ) : null}
        </div>
      ) : null}

      {loadState.kind === "ready" ? (
        <div data-testid="derivative-export-ready" className="space-y-4">
          <ExportArtifactCard view={loadState.view} />
          <AuditCard audit={loadState.view.audit} />
          <FormatActions
            view={loadState.view}
            formats={formats}
            onExport={(format) => void handleExport(format)}
          />
        </div>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Artifact provenance card
// ---------------------------------------------------------------------------

function ExportArtifactCard({ view }: { view: ExportView }) {
  return (
    <div
      data-testid="derivative-export-artifact"
      className="space-y-3 rounded-2xl border border-border/70 p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          data-testid="derivative-export-approved-badge"
          className="inline-flex items-center rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-800"
        >
          已批准 artifact
        </span>
        <span className="rounded-full border border-border bg-secondary px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
          {view.projectKey}
        </span>
      </div>
      <dl className="grid gap-2 text-[11px] sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <dt className="text-muted-foreground">preparation_id</dt>
          <dd data-testid="derivative-export-preparation-id" className="font-mono font-semibold">
            #{view.artifactId}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">revision</dt>
          <dd data-testid="derivative-export-revision" className="font-mono font-semibold">
            #{view.artifactRevisionId}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">export version</dt>
          <dd data-testid="derivative-export-version" className="font-mono font-semibold">
            v{view.exportVersion}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">manifest checksum</dt>
          <dd
            data-testid="derivative-export-manifest-checksum"
            title={view.manifestChecksum}
            className="font-mono font-semibold"
          >
            {shortExportHash(view.manifestChecksum)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">fork</dt>
          <dd data-testid="derivative-export-fork" className="font-mono font-semibold">
            {view.fork}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">approved counts</dt>
          <dd data-testid="derivative-export-counts" className="font-semibold">
            {view.counts.chapters} 章 · {view.counts.assets} 资产 ·{" "}
            {view.counts.citations} 引用 · {view.counts.revisions} 修订
          </dd>
        </div>
      </dl>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Three-dimension audit card (the ONLY quality source)
// ---------------------------------------------------------------------------

function AuditCard({ audit }: { audit: DerivativeExportAuditReport }) {
  return (
    <div
      data-testid="derivative-export-audit"
      data-verdict={audit.verdict}
      className="space-y-3 rounded-2xl border border-border/70 p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          三维状态审计
        </h4>
        <span
          data-testid="derivative-export-verdict"
          className={cn(
            "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
            audit.verdict === "blocked"
              ? "border-rose-600/50 bg-rose-600/10 text-rose-700"
              : "border-amber-500/40 bg-amber-500/10 text-amber-800"
          )}
        >
          {audit.verdict === "blocked" ? "阻断（不可发布）" : "合格候选"}
        </span>
      </div>

      <div className="space-y-2">
        {audit.dimensions.map((dimension) => (
          <AuditDimensionRow key={dimension.dimension} dimension={dimension} />
        ))}
      </div>

      {audit.blocked_reasons.length > 0 ? (
        <ul data-testid="derivative-export-blocked-reasons" className="space-y-1">
          {audit.blocked_reasons.map((reason) => (
            <li key={reason} className="font-mono text-[10px] leading-4 text-rose-700">
              {reason}
            </li>
          ))}
        </ul>
      ) : null}

      <p
        data-testid="derivative-export-phase22"
        className="text-[10px] text-muted-foreground"
      >
        Phase 22 资格证据：{audit.phase22.green_observed}/
        {audit.phase22.green_required} green（{audit.phase22.source}）— 质量维度
        反映真实状态，不能被导出通过替代。
      </p>
    </div>
  );
}

function AuditDimensionRow({
  dimension,
}: {
  dimension: DerivativeExportAuditDimension;
}) {
  return (
    <div
      data-testid="derivative-export-dimension"
      data-dimension={dimension.dimension}
      data-status={dimension.status}
      className="flex flex-wrap items-start justify-between gap-2 rounded-lg bg-secondary/40 px-3 py-2"
    >
      <div className="min-w-0">
        <span className="text-xs font-semibold">
          {AUDIT_DIMENSION_LABEL_TEXT[dimension.dimension] ?? dimension.dimension}
        </span>
        {dimension.blocked_reasons.length > 0 ? (
          <span className="mt-0.5 block font-mono text-[10px] leading-4 text-muted-foreground">
            {dimension.blocked_reasons.join("；")}
          </span>
        ) : null}
      </div>
      <span
        className={cn(
          "rounded-full border px-2 py-0.5 text-[11px] font-medium",
          AUDIT_STATUS_BADGE_CLASS[dimension.status] ??
            "border-border bg-muted text-muted-foreground"
        )}
      >
        {AUDIT_STATUS_LABEL_TEXT[dimension.status] ?? dimension.status}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Export actions: approved-artifact materialize -> verified download
// ---------------------------------------------------------------------------

function FormatActions({
  view,
  formats,
  onExport,
}: {
  view: ExportView;
  formats: Record<DerivativeExportFormat, FormatState>;
  onExport: (format: DerivativeExportFormat) => void;
}) {
  return (
    <div
      data-testid="derivative-export-actions"
      className="space-y-3 rounded-2xl border border-border/70 p-4"
    >
      <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        请求导出并下载
      </h4>
      <p className="text-[10px] leading-4 text-muted-foreground">
        每个格式按钮都会先提交已批准 artifact 的确定性 materialize 请求，再下载
        并校验 manifest 头；浏览器不会组装 manifest 或选择 live revision。下载完成
        不表示质量通过。
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {(["markdown", "epub"] as const).map((format) => {
          const state = formats[format];
          return (
            <div
              key={format}
              data-testid={`derivative-export-format-${format}`}
              data-status={state.status}
              className="space-y-2 rounded-xl border border-border/70 p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-semibold">
                  {FORMAT_LABEL_TEXT[format]}
                </span>
                <button
                  type="button"
                  data-testid={`derivative-export-button-${format}`}
                  aria-label={`导出并下载 ${FORMAT_LABEL_TEXT[format]}`}
                  className="rounded-full border border-border bg-background px-3 py-1 text-[11px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => onExport(format)}
                  disabled={state.status === "working"}
                >
                  {state.status === "working" ? "正在导出…" : "导出并下载"}
                </button>
              </div>

              {state.status === "working" ? (
                <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <Loader2 className="size-3 animate-spin" />
                  提交已批准 artifact 的 materialize 并校验 manifest…
                </p>
              ) : null}

              {state.status === "done" ? (
                <p
                  data-testid={`derivative-export-done-${format}`}
                  className="text-[11px] leading-4 text-emerald-800"
                >
                  {format === "markdown"
                    ? "已下载 · manifest 校验通过（与已批准 artifact 一致）"
                    : "已下载 · manifest 校验通过 · EPUB 互操作性未验证（无 EPUB validator，不标绿）"}
                </p>
              ) : null}

              {state.status === "error" ? (
                <div className="space-y-1.5">
                  <p
                    data-testid={`derivative-export-error-${format}`}
                    role="alert"
                    className="text-[11px] leading-4 text-rose-700"
                  >
                    {state.error ?? "导出失败"}
                  </p>
                  <button
                    type="button"
                    data-testid={`derivative-export-retry-${format}`}
                    className="rounded-lg border border-border px-2 py-0.5 text-[10px] font-semibold"
                    onClick={() => onExport(format)}
                  >
                    重试
                  </button>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
