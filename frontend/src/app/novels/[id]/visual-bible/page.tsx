"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { PageContainer } from "@/components/page-header";
import { VisualBibleEntitySheet } from "@/components/visual-bible/entity-sheet";
import { visualBibleApi, type VisualBibleVersionView } from "@/lib/visual-bible-api";

/**
 * Visual Bible workspace page slot (Phase 30-03). Loads the owner-scoped
 * Visual Bible versions for a novel and shows the candidate review sheet for
 * the latest version; everything stays candidate-only until explicit approval.
 */
export default function NovelVisualBiblePage() {
  const params = useParams<{ id: string }>();
  const novelId = params?.id;

  const [versions, setVersions] = useState<VisualBibleVersionView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await visualBibleApi.listVersions(novelId);
        if (cancelled) return;
        setVersions(res.data.items ?? []);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "加载 Visual Bible 失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [novelId]);

  if (novelId == null) {
    return null;
  }

  if (loading) {
    return (
      <PageContainer>
        <p data-testid="visual-bible-loading" className="text-sm text-muted-foreground">
          正在加载 Visual Bible…
        </p>
      </PageContainer>
    );
  }

  if (error) {
    return (
      <PageContainer>
        <p data-testid="visual-bible-error" className="text-sm text-rose-700">
          {error}
        </p>
      </PageContainer>
    );
  }

  if (versions.length === 0) {
    return (
      <PageContainer>
        <p data-testid="visual-bible-empty" className="text-sm text-muted-foreground">
          暂无 Visual Bible 版本
        </p>
      </PageContainer>
    );
  }

  const latest = versions[0];

  return (
    <PageContainer className="space-y-6">
      <VisualBibleEntitySheet novelId={novelId} versionId={latest.id} />
    </PageContainer>
  );
}
