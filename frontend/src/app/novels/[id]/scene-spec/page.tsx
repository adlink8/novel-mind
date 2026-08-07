"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";

import { PageContainer } from "@/components/page-header";
import { PromptDiff } from "@/components/scene-spec/diff";
import { SceneSpecPreview } from "@/components/scene-spec/preview";
import { sceneSpecsApi, type SceneSpecView } from "@/lib/scene-spec-api";

/**
 * Scene Spec workspace page slot (Phase 32-05). Lists the compiled candidate
 * scene specs for a novel and shows the preview for the latest one; the
 * `?diff=<revisionId>` query switches to the deterministic recompile diff.
 * The page never assembles a provider prompt — previews stay server-side.
 */
export default function NovelSceneSpecPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const novelId = params?.id;
  const diffRaw = searchParams?.get("diff");

  const [specs, setSpecs] = useState<SceneSpecView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (novelId == null) return;
    setLoading(true);
    setError(null);
    try {
      const res = await sceneSpecsApi.listSpecs(novelId);
      setSpecs(res.data.items ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 Scene Spec 失败");
    } finally {
      setLoading(false);
    }
  }, [novelId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (novelId == null) {
    return null;
  }

  if (loading) {
    return (
      <PageContainer>
        <p data-testid="scene-spec-loading" className="text-sm text-muted-foreground">
          正在加载 Scene Spec…
        </p>
      </PageContainer>
    );
  }

  if (error) {
    return (
      <PageContainer>
        <p data-testid="scene-spec-error" className="text-sm text-rose-700">
          {error}
        </p>
      </PageContainer>
    );
  }

  if (diffRaw != null) {
    const revisionId = Number(diffRaw);
    if (Number.isFinite(revisionId)) {
      return (
        <PageContainer className="space-y-6">
          <PromptDiff novelId={novelId} revisionId={revisionId} />
        </PageContainer>
      );
    }
  }

  if (specs.length === 0) {
    return (
      <PageContainer>
        <p data-testid="scene-spec-empty" className="text-sm text-muted-foreground">
          暂无 Scene Spec
        </p>
      </PageContainer>
    );
  }

  const latest = specs[0];

  return (
    <PageContainer className="space-y-6">
      <SceneSpecPreview novelId={novelId} specId={latest.id} />
    </PageContainer>
  );
}
