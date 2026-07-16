"use client";

import Link from "next/link";

import { NM_NODE_BADGE_LABEL } from "@/lib/narrative-memory-api";
import type {
  NmClaimItem,
  NmSourceLinkItem,
} from "@/lib/narrative-memory-api";
import {
  formatChapterRange,
  nodeKindLabel,
  type StructureNodeSelection,
  type StructureSource,
} from "./structure-types";

type Props = {
  selected: StructureNodeSelection | null;
  structureSource: StructureSource;
  claims?: NmClaimItem[];
  claimsLoading?: boolean;
  claimsError?: string | null;
  /** Claim currently drilled for source-links (NM only). */
  selectedClaimId?: number | null;
  onClaimSelect?: (claim: NmClaimItem) => void;
  sourceLinks?: NmSourceLinkItem[];
  sourceLinksLoading?: boolean;
  sourceLinksError?: string | null;
  /** Used for reader deep-links from source evidence. */
  novelId?: string;
  className?: string;
};

function shortHash(hash: string | null | undefined): string {
  if (!hash) return "";
  const t = hash.trim();
  if (t.length <= 12) return t;
  return `${t.slice(0, 8)}…`;
}

function readerHref(
  novelId: string,
  link: NmSourceLinkItem
): string {
  const chapter = link.chapter_number;
  const start = link.source_start;
  const params = new URLSearchParams();
  params.set("chapter", String(chapter));
  if (typeof start === "number" && Number.isFinite(start)) {
    params.set("start", String(start));
  }
  params.set("from", "structure");
  return `/novels/${novelId}?${params.toString()}`;
}

export function StructureNodePanel({
  selected,
  structureSource,
  claims = [],
  claimsLoading = false,
  claimsError = null,
  selectedClaimId = null,
  onClaimSelect,
  sourceLinks = [],
  sourceLinksLoading = false,
  sourceLinksError = null,
  novelId,
  className,
}: Props) {
  if (!selected) {
    return (
      <div
        className={
          className ??
          "rounded-xl border border-dashed bg-card/40 p-3 text-xs text-muted-foreground"
        }
      >
        在左侧选择结构节点，以限定时间线 / 关系 / 线索的章节范围。
      </div>
    );
  }

  const isNm =
    structureSource === "narrative_memory" && selected.nmNodeId != null;

  return (
    <div
      className={
        className ??
        "rounded-xl border bg-card/70 p-3 text-sm shadow-sm"
      }
      data-testid="structure-node-panel"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {nodeKindLabel(selected.kind)}
          </p>
          <p className="mt-0.5 font-serif text-base font-semibold leading-snug">
            {selected.label}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            视图范围：{formatChapterRange(selected.chapterStart, selected.chapterEnd)}
          </p>
        </div>
        {isNm && (
          <span
            data-testid="nm-node-badge"
            className="shrink-0 rounded-full border border-violet-300/80 bg-violet-50 px-2 py-0.5 text-[10px] font-medium text-violet-950"
          >
            {NM_NODE_BADGE_LABEL}
          </span>
        )}
      </div>

      {isNm && (
        <div className="mt-3 border-t pt-2">
          <p className="text-[11px] font-medium text-muted-foreground">
            节点声明（候选）
          </p>
          {claimsLoading && (
            <p className="mt-1 text-xs text-muted-foreground">加载声明…</p>
          )}
          {claimsError && (
            <p className="mt-1 text-xs text-destructive" role="alert">
              {claimsError}
            </p>
          )}
          {!claimsLoading && !claimsError && claims.length === 0 && (
            <p
              className="mt-1 text-xs text-muted-foreground"
              data-testid="claims-empty-honesty"
            >
              此节点暂无可见声明。
            </p>
          )}
          {!claimsLoading && claims.length > 0 && (
            <ul className="mt-1.5 max-h-36 space-y-1.5 overflow-y-auto">
              {claims.map((c) => {
                const active = selectedClaimId === c.id;
                return (
                  <li key={c.id}>
                    <button
                      type="button"
                      data-testid={`nm-claim-${c.id}`}
                      aria-pressed={active}
                      onClick={() => onClaimSelect?.(c)}
                      className={`w-full rounded-lg border px-2 py-1.5 text-left text-xs transition-colors ${
                        active
                          ? "border-violet-400 bg-violet-50/80"
                          : "bg-background/80 hover:bg-muted/60"
                      }`}
                    >
                      <span className="text-[10px] text-muted-foreground">
                        {c.claim_kind}
                      </span>
                      <p className="mt-0.5 line-clamp-2 text-foreground/90">
                        {c.summary || c.text || "（无摘要）"}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {selectedClaimId != null && (
            <div
              className="mt-2 rounded-lg border border-dashed bg-background/60 p-2"
              data-testid="source-links-panel"
            >
              <p className="text-[11px] font-medium text-muted-foreground">
                叶子证据链接
              </p>
              {sourceLinksLoading && (
                <p className="mt-1 text-xs text-muted-foreground">
                  加载证据链接…
                </p>
              )}
              {sourceLinksError && (
                <p className="mt-1 text-xs text-destructive" role="alert">
                  {sourceLinksError}
                </p>
              )}
              {!sourceLinksLoading &&
                !sourceLinksError &&
                sourceLinks.length === 0 && (
                  <p
                    className="mt-1 text-xs text-muted-foreground"
                    data-testid="source-links-empty-honesty"
                  >
                    无叶子证据链接
                  </p>
                )}
              {!sourceLinksLoading && sourceLinks.length > 0 && (
                <ul className="mt-1.5 max-h-40 space-y-1.5 overflow-y-auto">
                  {sourceLinks
                    .filter((l) => l.claim_id === selectedClaimId)
                    .map((link) => {
                      const hash = shortHash(link.content_hash);
                      const offset =
                        typeof link.source_start === "number"
                          ? `${link.source_start}–${link.source_end}`
                          : "";
                      const body = (
                        <>
                          <span className="font-medium">
                            第 {link.chapter_number} 章
                          </span>
                          {offset && (
                            <span className="ml-1.5 text-muted-foreground">
                              offset {offset}
                            </span>
                          )}
                          {hash && (
                            <span className="ml-1.5 font-mono text-[10px] text-muted-foreground">
                              {hash}
                            </span>
                          )}
                          <span className="mt-0.5 block text-[10px] text-muted-foreground">
                            {link.source_kind}
                          </span>
                        </>
                      );
                      return (
                        <li
                          key={link.id}
                          data-testid={`source-link-${link.id}`}
                          className="rounded-md border bg-card px-2 py-1.5 text-xs"
                        >
                          {novelId ? (
                            <Link
                              href={readerHref(novelId, link)}
                              className="block text-foreground hover:text-primary"
                            >
                              {body}
                            </Link>
                          ) : (
                            body
                          )}
                        </li>
                      );
                    })}
                </ul>
              )}
              {!sourceLinksLoading &&
                sourceLinks.length > 0 &&
                sourceLinks.filter((l) => l.claim_id === selectedClaimId)
                  .length === 0 && (
                  <p
                    className="mt-1 text-xs text-muted-foreground"
                    data-testid="source-links-empty-honesty"
                  >
                    无叶子证据链接
                  </p>
                )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
