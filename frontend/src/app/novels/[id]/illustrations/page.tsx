"use client";

import { useParams } from "next/navigation";

import { IllustrationGallery } from "@/components/illustrations/gallery";
import { PageContainer } from "@/components/page-header";

/**
 * Illustration gallery page slot (Phase 33-04). Mounts the owner-scoped
 * candidate gallery for a novel; assets stay candidate-only until explicit
 * approval, and the gallery never presents a generated candidate as
 * reader-visible canon.
 */
export default function NovelIllustrationsPage() {
  const params = useParams<{ id: string }>();
  const novelId = params?.id;

  if (novelId == null) {
    return null;
  }

  return (
    <PageContainer>
      <IllustrationGallery novelId={novelId} />
    </PageContainer>
  );
}
