"use client";

import { useParams } from "next/navigation";

import { KeySceneReviewWorkspace } from "@/components/key-scenes/review";
import { PageContainer } from "@/components/page-header";

/**
 * Key Scene review workspace page slot (Phase 31-04). Renders the owner-scoped
 * candidate review for one key-scene set; candidates stay candidate-only until
 * explicit approval/freeze.
 */
export default function KeySceneSetPage() {
  const params = useParams<{ id: string; setId: string }>();
  const novelId = params?.id;
  const setId = Number(params?.setId);

  if (novelId == null || !Number.isFinite(setId)) {
    return null;
  }

  return (
    <PageContainer className="space-y-6">
      <KeySceneReviewWorkspace novelId={novelId} setId={setId} />
    </PageContainer>
  );
}
