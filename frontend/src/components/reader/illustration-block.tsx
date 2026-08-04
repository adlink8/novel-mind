"use client";

/**
 * Phase 34-02 — Reader-safe inline figure for an approved illustration
 * (REQ-VIS-05, D-34-01/D-34-02).
 *
 * `IllustrationBlock` renders only a server-published `valid` anchor whose
 * hash/range replay against the current chapter content. It is a flow-layout
 * sibling block: it never overlaps input/progress/navigation controls, exposes
 * an accessible `<figure>`/`<figcaption>` with plain-text caption/alt (no
 * `dangerouslySetInnerHTML`), lazy-loads the approved asset bytes with the
 * owner-scoped axios client (Bearer auth), and degrades gracefully when the
 * binary is missing or the anchor is stale/invalid.
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ImageOff } from "lucide-react";

import { api } from "@/lib/api";
import {
  ANCHOR_REASON_LABELS,
  ANCHOR_STATUS_LABELS,
  illustrationAssetBytesUrl,
  verifyAnchorAgainstChapter,
  type AnchorVerificationResult,
  type IllustrationAnchorView,
} from "@/lib/illustration-anchor";

type AssetLoadState = "idle" | "loading" | "ready" | "missing";

export interface IllustrationBlockProps {
  anchor: IllustrationAnchorView;
  novelId: string | number;
  /** Current chapter content; the anchor hash is re-verified against it. */
  chapterContent: string;
  /** Direct image src override (tests/SSR); default is the owner-scoped bytes URL. */
  assetUrl?: string;
  /** Byte fetcher override (tests); default uses the shared axios client. */
  assetFetcher?: (src: string) => Promise<Blob>;
}

const FALLBACK_FETCHER = async (src: string): Promise<Blob> => {
  const res = await api.get<Blob>(src, { responseType: "blob" });
  return res.data;
};

export function IllustrationBlock({
  anchor,
  novelId,
  chapterContent,
  assetUrl,
  assetFetcher = FALLBACK_FETCHER,
}: IllustrationBlockProps) {
  const [verification, setVerification] = useState<AnchorVerificationResult | null>(
    null
  );
  const [assetState, setAssetState] = useState<AssetLoadState>("idle");
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const rootRef = useRef<HTMLElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);

  // D-34-01: re-verify the exact source hash/range against current content
  // before any approved asset may render.
  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset the pending verification when the anchor/chapter changes
    setVerification(null);
    void verifyAnchorAgainstChapter(anchor, chapterContent).then((result) => {
      if (!cancelled) setVerification(result);
    });
    return () => {
      cancelled = true;
    };
  }, [anchor, chapterContent]);

  // Lazy load: only start the bytes fetch when the figure approaches the
  // viewport (jsdom/SSR fall back to loading immediately).
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- jsdom/SSR fallback starts the lazy fetch immediately
      setAssetState((s) => (s === "idle" ? "loading" : s));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setAssetState((s) => (s === "idle" ? "loading" : s));
          observer.disconnect();
        }
      },
      { rootMargin: "240px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Fetch the approved asset bytes (auth attached) → object URL; a failed
  // fetch is an explicit "missing asset", never a silent drop.
  useEffect(() => {
    if (assetState !== "loading" || !verification?.ok) return;
    if (assetUrl) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- explicit src override is applied once the anchor verifies
      setObjectUrl(assetUrl);
      setAssetState("ready");
      return;
    }
    let cancelled = false;
    const src = illustrationAssetBytesUrl(
      novelId,
      anchor.published_asset_revision_id
    );
    assetFetcher(src)
      .then((blob) => {
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        objectUrlRef.current = url;
        setObjectUrl(url);
        setAssetState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setAssetState("missing");
      });
    return () => {
      cancelled = true;
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, [assetState, verification, assetUrl, novelId, anchor.published_asset_revision_id, assetFetcher]);

  if (!verification) {
    // Verifying (async sha256) — keep the figure slot reserved, no overlap.
    return (
      <figure
        ref={rootRef}
        data-testid="illustration-block"
        data-anchor-id={anchor.id}
        data-anchor-status="verifying"
        data-reader-illustration
        aria-busy="true"
        className="relative my-8 flex flex-col items-center"
      >
        <div className="flex min-h-24 w-full max-w-2xl flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-border/70 bg-muted/30 px-4 py-8 text-sm text-muted-foreground">
          <span>插图校验中…</span>
        </div>
      </figure>
    );
  }

  if (!verification.ok) {
    // Stale/invalid/unapproved anchor: explicit placeholder, never an image.
    return (
      <figure
        ref={rootRef}
        data-testid="illustration-block"
        data-anchor-id={anchor.id}
        data-anchor-status={verification.status}
        data-reason={verification.reasonCode}
        data-reader-illustration
        className="relative my-8 flex flex-col items-center"
      >
        <div className="flex w-full max-w-2xl items-start gap-3 rounded-2xl border border-amber-300/70 bg-amber-50 px-4 py-4 text-amber-950">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div className="min-w-0">
            <p
              data-testid="illustration-placeholder"
              className="text-sm font-medium"
            >
              插图{ANCHOR_STATUS_LABELS[verification.status] ?? "不可用"}
            </p>
            <p className="mt-1 text-xs text-amber-900/80">
              {ANCHOR_REASON_LABELS[verification.reasonCode] ??
                verification.detail}
            </p>
            {anchor.caption ? (
              <p
                data-testid="illustration-caption"
                className="mt-2 text-xs text-muted-foreground"
              >
                {anchor.caption}
              </p>
            ) : null}
          </div>
        </div>
      </figure>
    );
  }

  const missing =
    assetState === "missing" ||
    (assetState === "ready" && !objectUrl && !assetUrl);

  return (
    <figure
      ref={rootRef}
      data-testid="illustration-block"
      data-anchor-id={anchor.id}
      data-anchor-status="valid"
      data-reader-illustration
      className="relative my-8 flex w-full max-w-2xl flex-col items-center self-center"
    >
      {missing ? (
        // Graceful missing asset: accessible placeholder with caption retained.
        <div
          data-testid="illustration-missing"
          className="flex w-full flex-col items-center gap-2 rounded-2xl border border-dashed border-border/70 bg-muted/30 px-4 py-8 text-center"
        >
          <ImageOff className="size-5 text-muted-foreground" aria-hidden />
          <p className="text-sm font-medium text-muted-foreground">插图缺失</p>
        </div>
      ) : assetState === "loading" || !objectUrl ? (
        <div
          data-testid="illustration-loading"
          aria-busy="true"
          className="flex min-h-24 w-full items-center justify-center rounded-2xl border border-dashed border-border/70 bg-muted/30 px-4 py-8 text-sm text-muted-foreground"
        >
          插图加载中…
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element -- asset is a fetch-blob object URL (owner-scoped bytes), not a next/image-servable URL
        <img
          data-testid="illustration-image"
          src={objectUrl}
          alt={anchor.alt_text || anchor.caption || "章节插图"}
          className="h-auto max-h-[70vh] w-full rounded-2xl border border-border/60 object-contain shadow-sm"
          onError={() => setAssetState("missing")}
        />
      )}
      <figcaption className="mt-3 w-full max-w-2xl text-center">
        <span
          data-testid="illustration-caption"
          className="block text-sm text-foreground/90"
        >
          {anchor.caption}
        </span>
        {anchor.citation ? (
          <span
            data-testid="illustration-citation"
            className="mt-0.5 block text-xs text-muted-foreground"
          >
            引用：{anchor.citation}
          </span>
        ) : null}
      </figcaption>
    </figure>
  );
}
