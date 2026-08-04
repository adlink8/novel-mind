"use client";

/**
 * Phase 25.3-05 — CitedAnswerArtifact React renderer + external_evidence display
 * discipline (D-08/D-09).
 *
 * pi-web-ui influence is **design-only** (D-03 selective verdict): the
 * registerToolRenderer / registerMessageRenderer registry shape becomes a plain
 * React record (`ARTIFACT_RENDERERS`) — zero imports from the pi-web-ui npm
 * package. Shared reader-chat components
 * (MessageBubble / CitationChip, Phase 25.1) are composed, not duplicated;
 * citation jumps follow the analysis-chat-panel convention
 * (`/novels/{id}?chapter=..&start=..&from=timeline`).
 */

import type { ComponentType } from "react";
import { useRouter } from "next/navigation";

import {
  CitationChip,
  type CitationNavigateTarget,
} from "@/components/reader/reader-chat-panel";
import type { ArtifactView, CitationView } from "@/lib/api";

export type ArtifactRendererProps = {
  artifact: ArtifactView;
  novelId: string;
  /** Optional override; defaults to the analysis-chat-panel jump convention. */
  onCitationNavigate?: (target: CitationNavigateTarget) => void;
};

/** External evidence D-09 字段（25.3-03 schema）客户端镜像。 */
export interface ExternalEvidenceSource {
  server: string;
  tool: string;
  uri: string;
  title: string;
  retrieved_from?: string;
}

export interface ExternalEvidenceClaim {
  text: string;
  source_index: number;
}

export interface ExternalEvidenceContent {
  sources?: ExternalEvidenceSource[];
  retrieval_time?: string;
  claims?: ExternalEvidenceClaim[];
  confidence?: "low" | "medium" | "high";
  prohibited_from_canon?: boolean;
  release_status?: string;
}

/** 持久可见标签——外部证据禁止被伪装成正典引用（D-08/D-09，UI 层强制）。 */
export const EXTERNAL_EVIDENCE_LABEL = "External evidence — prohibited from canon";

/** 把 answer_blocks 的引证映射为共享 CitationView 形状（每证据引用一个芯片）。 */
function toCitationViews(artifact: ArtifactView): CitationView[] {
  const blocks = artifact.content?.answer?.answer_blocks ?? [];
  const out: CitationView[] = [];
  for (const block of blocks) {
    for (const c of block.citations ?? []) {
      out.push({
        block_id: c.block_id ?? `artifact-${artifact.id}`,
        evidence_key: c.evidence_key,
        context_evidence_ref_id: c.context_evidence_ref_id ?? 0,
        chapter_id: c.chapter_id,
        source_start: c.source_start,
        source_end: c.source_end,
      });
    }
  }
  return out;
}

/** Cited Answer 渲染：答案块 + 每个证据引用一个 CitationChip，点击跳原文。 */
export function CitedAnswerArtifactView({
  artifact,
  novelId,
  onCitationNavigate,
}: ArtifactRendererProps) {
  const router = useRouter();
  const blocks = artifact.content?.answer?.answer_blocks ?? [];
  const citations = toCitationViews(artifact);

  const handleNavigate = (target: CitationNavigateTarget) => {
    if (onCitationNavigate) {
      onCitationNavigate(target);
      return;
    }
    const params = new URLSearchParams();
    params.set("chapter", String(target.chapter_id));
    params.set("start", String(target.source_start));
    params.set("from", "timeline");
    router.push(`/novels/${novelId}?${params.toString()}`);
  };

  return (
    <div
      data-testid="analysis-artifact-cited-answer"
      className="space-y-2"
    >
      {blocks.map((block, i) =>
        block.text ? (
          <p
            key={`${artifact.id}-block-${i}`}
            className="whitespace-pre-wrap text-[13px] leading-relaxed"
          >
            {block.text}
          </p>
        ) : null
      )}
      {citations.length > 0 ? (
        <div className="mt-1 flex flex-wrap gap-1.5">
          {citations.map((c, i) => (
            <CitationChip
              key={`${artifact.id}-cit-${i}`}
              citation={c}
              onNavigate={handleNavigate}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * External Evidence 渲染（25.3-03 D-09 schema）：sources/claims/confidence 持久
 * 可见标签下展示，**无**任何 reader-citation 按钮——UI 无法把外部主张伪装成
 * 正典风格的引用（T-25.3-05-02，Pitfall 6）。
 */
export function ExternalEvidenceView({ artifact }: ArtifactRendererProps) {
  const content = (artifact.content ?? {}) as ExternalEvidenceContent;
  const sources = content.sources ?? [];
  const claims = content.claims ?? [];

  return (
    <div
      data-testid="analysis-artifact-external-evidence"
      className="space-y-1.5"
    >
      <p
        data-testid="analysis-artifact-external-label"
        className="rounded bg-amber-500/10 px-2 py-1 text-[11px] font-medium text-amber-900"
      >
        {EXTERNAL_EVIDENCE_LABEL}
      </p>
      {sources.length > 0 ? (
        <ul className="space-y-1">
          {sources.map((s, i) => (
            <li key={`${s.server}-${s.tool}-${i}`} className="text-[12px]">
              <span className="font-medium text-foreground">{s.title}</span>
              <span className="text-muted-foreground">
                {" "}
                · {s.server}/{s.tool}
              </span>
              <span className="block truncate text-[11px] text-muted-foreground">
                {s.uri}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {claims.length > 0 ? (
        <ul className="space-y-1">
          {claims.map((c, i) => (
            <li
              key={`claim-${i}`}
              className="whitespace-pre-wrap text-[12px] leading-relaxed"
            >
              {c.text}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="text-[11px] text-muted-foreground">
        confidence {content.confidence ?? "low"} · retrieved{" "}
        {content.retrieval_time ?? "—"}
      </p>
    </div>
  );
}

/** 未知/畸形产物类型：显式 fallback，永不崩溃（T-25.3-05-05）。 */
export function UnknownArtifactFallback({ artifact }: ArtifactRendererProps) {
  return (
    <div
      data-testid="analysis-artifact-unknown"
      className="text-[12px] text-muted-foreground"
    >
      未知产物类型：{artifact.type}（无法预览）
    </div>
  );
}

/** 类型键 → 渲染器（registerToolRenderer 形状，纯 React 记录，零 pi-web-ui import）。 */
export const ARTIFACT_RENDERERS = {
  cited_answer: CitedAnswerArtifactView,
  external_evidence: ExternalEvidenceView,
} as const;

export type ArtifactRenderer = ComponentType<ArtifactRendererProps>;

export function resolveArtifactRenderer(type: string): ArtifactRenderer {
  return (
    ARTIFACT_RENDERERS[type as keyof typeof ARTIFACT_RENDERERS] ??
    UnknownArtifactFallback
  );
}

/** Analysis workspace artifact preview：按类型经注册表解析并渲染。 */
export function ArtifactPreview({
  artifact,
  novelId,
  onCitationNavigate,
}: ArtifactRendererProps) {
  // ARTIFACT_RENDERERS 是模块级常量注册表（组件定义恒在模块顶层），
  // 这里经受控条件选择静态组件引用，渲染期不会重新创建组件（state 稳定）。
  const type = artifact.type;
  const Renderer =
    type === "cited_answer"
      ? CitedAnswerArtifactView
      : type === "external_evidence"
        ? ExternalEvidenceView
        : UnknownArtifactFallback;
  return (
    <Renderer
      artifact={artifact}
      novelId={novelId}
      onCitationNavigate={onCitationNavigate}
    />
  );
}
