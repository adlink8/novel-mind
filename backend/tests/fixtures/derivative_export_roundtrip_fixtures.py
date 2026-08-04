"""Frozen Phase 39-01 derivative export round-trip fixtures (D-39-01/D-39-02).

Deterministic, database-free fixture builders shared by the unit / adversarial /
security suites:

- frozen revision/asset/citation/version/lineage fixture data (37-04
  ``PublishedDerivativeRevision`` + 38-03/04 ``PublishedDerivativeVisualAsset``);
- a ready-to-seal ``ExportSnapshot`` builder with a known owner/project/fork/
  snapshot lineage so Markdown/EPUB3 round-trips are byte/hash reproducible.

Nothing here touches the database; integration tests build their own real rows
and import the pure contracts only.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.derivative_visual_asset import (
    DERIVATIVE_ASSET_NAMESPACE,
    DerivativeAssetIdentityRow,
    DerivativeAssetReviewEnvelope,
    DerivativeAssetReviewEventView,
    DerivativeAssetSourceRef,
    DerivativeConsistencyReport,
    DerivativeConsistencyVerdict,
    DerivativeSourceSnapshotRef,
    DerivativeVisualAssetState,
    DerivativeVisualVersionRef,
    PublishedDerivativeVisualAsset,
)
from app.services.derivative_export.manifest import (
    DerivativeExportAsset,
    DerivativeExportChapter,
    DerivativeExportCitation,
    DerivativeExportManifest,
    DerivativeExportRevision,
    MissingDerivativeAssetRecord,
    derivative_export_manifest_hash,
)
from app.services.derivative_export.snapshot import (
    ExportSnapshot,
    seal_export_snapshot,
)
from app.services.derivative_generation.published_revision import (
    PublishedDerivativeRevision,
    build_published_derivative_revision,
    canonical_citation_hash,
)

# Frozen fixture lineage (one version for all fixture data).
OWNER_ID = 101
NOVEL_ID = 202
PROJECT_ID = 303
PROJECT_KEY = "fixture-project"
PROJECT_NAME = "Fixture Derivative Project"
FORK_ID = 404
FORK_KEY = "ff-fixture"
SPACE = "fanfiction_canon"

HEX64_A = "a" * 64  # source snapshot hash
HEX64_B = "b" * 64  # project manifest hash
HEX64_C = "c" * 64  # cutoff snapshot hash
HEX64_D = "d" * 64  # scope hash
HEX64_E = "e" * 64  # scene spec hash
HEX64_F = "f" * 64  # divergence manifest hash
HEX64_0 = "0" * 64

CITATION_KEYS = ("fork:ff-fixture:chapter:1", "fork:ff-fixture:chapter:2")
FIXED_APPROVED_AT = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def fixture_citation_hash(keys: list[str] | tuple[str, ...] = CITATION_KEYS) -> str:
    return canonical_citation_hash(list(keys))


def fixture_revision(
    *,
    revision_id: int = 501,
    version_id: int = 1,
    citation_keys: list[str] | None = None,
    asset_hashes: list[str] | None = None,
    source_snapshot: str = HEX64_A,
    manifest_hash: str = HEX64_B,
) -> PublishedDerivativeRevision:
    keys = list(citation_keys if citation_keys is not None else CITATION_KEYS[:1])
    return build_published_derivative_revision(
        owner_id=OWNER_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        revision_id=revision_id,
        version_id=version_id,
        source_snapshot=source_snapshot,
        manifest_hash=manifest_hash,
        citation_keys=keys,
        approval_state="approved",
        approver_id=OWNER_ID,
        approved_at=FIXED_APPROVED_AT,
        approval_reason="owner approved the fixture divergence",
        override_kind="character",
        override_reason="fixture divergence reason",
        gate_verdict="needs_override",
        gate_reason="declared_canon_delta",
        canon_delta_hash=HEX64_C,
        evidence_snapshot={
            "gate_verdict": "needs_override",
            "gate_reason": "declared_canon_delta",
            "canon_delta_hash": HEX64_C,
            "divergence": {"divergence_type": "character"},
            "kind": "character",
            "reason": "fixture divergence reason",
            "affected_evidence": ["fork:ff-fixture:chapter:1"],
            "citation_keys": keys,
            "package_hash": HEX64_D,
            "prompt_hash": HEX64_E,
        },
        asset_hashes=asset_hashes,
    )


def fixture_asset(
    *,
    asset_id: str = "dv-fixture-asset",
    asset_key: str = "fixture-asset-key",
    content_hash: str = HEX64_F,
    chapter_number: int = 1,
    visual_version_id: int = 701,
    review_state: str = "approved",
    size_bytes: int = 68,
) -> PublishedDerivativeVisualAsset:
    return PublishedDerivativeVisualAsset(
        id=801,
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        asset_id=asset_id,
        asset_key=asset_key,
        content_hash=content_hash,
        mime_type="image/png",
        size_bytes=size_bytes,
        namespace=DERIVATIVE_ASSET_NAMESPACE,
        scene_spec_hash=HEX64_E,
        chapter_number=chapter_number,
        visual_version=DerivativeVisualVersionRef(
            version_id=visual_version_id,
            version_key="dv-version-1",
            version_hash=HEX64_C,
        ),
        source_snapshot=DerivativeSourceSnapshotRef(
            source_snapshot_id="snap-1",
            source_snapshot_hash=HEX64_A,
            source_manifest_hash=HEX64_B,
            cutoff_chapter=8,
        ),
        approval=DerivativeVisualAssetState(review_state),
        review=DerivativeAssetReviewEnvelope(
            review_state=DerivativeVisualAssetState(review_state),
            consistency_verdict=DerivativeConsistencyVerdict.PASS,
            consistency_report=DerivativeConsistencyReport(
                evaluator_id="derivative-visual-consistency.cross_chapter.v1",
                evaluator_version="1.0.0",
                verdict=DerivativeConsistencyVerdict.PASS,
            ),
            reasons=[],
            review_events=[
                DerivativeAssetReviewEventView(
                    action="approve",
                    actor_source="human",
                    actor="owner",
                    reason="fixture approval",
                    event_key="evt-approve-1",
                    from_review_state=DerivativeVisualAssetState.CANDIDATE,
                    to_review_state=DerivativeVisualAssetState.APPROVED,
                )
            ],
        ),
        source_refs=[
            DerivativeAssetSourceRef(
                asset_key="source-1",
                asset_id="source-asset-1",
                source_asset_id="original-asset-1",
                source_bytes_hash=HEX64_D,
            )
        ],
        identity_lineage=[
            DerivativeAssetIdentityRow(
                stable_id="fixture-hero",
                entity_key="hero",
                entity_type="character",
                source_entity_hash=HEX64_C,
            )
        ],
        generator_lineage={
            "provider": "mock",
            "provider_model": "mock-img-v1",
            "prompt_hash": HEX64_E,
            "runtime": {},
        },
        divergence_manifest_hash=HEX64_F,
    )


def fixture_chapter(
    *,
    chapter_id: int = 601,
    position: int = 0,
    title: str = "Fixture Chapter 1",
    content: str = "阿宁在竹林入口站定，深吸一口气。\n\n她推开了那扇竹门。",
    version_id: int = 1,
    revision_id: int | None = 501,
) -> DerivativeExportChapter:
    import hashlib

    from app.services.derivative_editor.chapters import canonicalize_markdown

    canonical = canonicalize_markdown(content)
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return DerivativeExportChapter(
        chapter_id=chapter_id,
        position=position,
        chapter_number=position + 1,
        title=title,
        content=canonical,
        content_hash=content_hash,
        markdown_checksum=content_hash,
        version_id=version_id,
        revision_id=revision_id,
    )


def fixture_export_revision(
    *,
    revision_id: int = 501,
    version_id: int = 1,
    chapter_id: int = 601,
    chapter_number: int = 1,
    asset_hashes: tuple[str, ...] = (),
) -> DerivativeExportRevision:
    revision = fixture_revision(
        revision_id=revision_id,
        version_id=version_id,
        asset_hashes=list(asset_hashes),
    )
    return DerivativeExportRevision(
        owner_id=revision.owner_id,
        project_id=revision.project_id,
        fork_id=revision.fork_id,
        revision_id=revision.revision_id,
        version_id=revision.version_id,
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        status=revision.status,
        source_snapshot=revision.source_snapshot,
        manifest_hash=revision.manifest_hash,
        citation_hash=revision.citation_hash,
        asset_hashes=tuple(revision.asset_hashes),
        approval=dict(revision.approval),
        review=dict(revision.review),
    )


def fixture_export_asset(
    asset: PublishedDerivativeVisualAsset | None = None,
) -> DerivativeExportAsset:
    from app.services.derivative_export.snapshot import _to_export_asset

    return _to_export_asset(asset or fixture_asset())


def fixture_export_citations(
    *, revision_id: int = 501, chapter_number: int = 1
) -> tuple[DerivativeExportCitation, ...]:
    return tuple(
        DerivativeExportCitation(
            citation_key=key,
            citation_hash=canonical_citation_hash([key]),
            source_snapshot=HEX64_A,
            revision_id=revision_id,
            chapter_number=chapter_number,
        )
        for key in CITATION_KEYS[:1]
    )


def build_fixture_snapshot(
    *,
    chapters: tuple[DerivativeExportChapter, ...] | None = None,
    revisions: tuple[DerivativeExportRevision, ...] | None = None,
    assets: tuple[DerivativeExportAsset, ...] | None = None,
    citations: tuple[DerivativeExportCitation, ...] | None = None,
    missing_assets: tuple[MissingDerivativeAssetRecord, ...] = (),
    text_version_hash: str = HEX64_0,
) -> ExportSnapshot:
    """Build a sealed, fully deterministic fixture snapshot."""
    import hashlib

    if chapters is None:
        chapters = (fixture_chapter(),)
    if revisions is None:
        revisions = (fixture_export_revision(),)
    if assets is None:
        assets = (fixture_export_asset(),)
    if citations is None:
        citations = fixture_export_citations()
    if text_version_hash == HEX64_0:
        text_version_hash = _fixture_text_version_hash(chapters)
    snapshot = ExportSnapshot(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        project_key=PROJECT_KEY,
        project_name=PROJECT_NAME,
        fork_id=FORK_ID,
        fork_key=FORK_KEY,
        source_snapshot=HEX64_A,
        project_manifest_hash=HEX64_B,
        cutoff_snapshot_hash=HEX64_C,
        scope_hash=HEX64_D,
        text_version_hash=text_version_hash,
        chapters=chapters,
        revisions=revisions,
        assets=assets,
        citations=citations,
        missing_assets=missing_assets,
        snapshot_hash="0" * 64,
    )
    return seal_export_snapshot(snapshot)


def _fixture_text_version_hash(
    chapters: tuple[DerivativeExportChapter, ...],
) -> str:
    import json

    from app.services.derivative_export.manifest import canonical_export_hash

    return canonical_export_hash(
        {
            "chapters": [
                {
                    "chapter_number": c.chapter_number,
                    "content_hash": c.content_hash,
                    "version_id": c.version_id,
                }
                for c in chapters
            ]
        }
    )


def seal_fixture_manifest(snapshot: ExportSnapshot) -> DerivativeExportManifest:
    """Seal the manifest for a fixture snapshot (manifest_hash == snapshot hash)."""
    from app.services.derivative_export.manifest import seal_derivative_export_manifest

    return seal_derivative_export_manifest(snapshot)


__all__ = [
    "CITATION_KEYS",
    "FORK_ID",
    "FORK_KEY",
    "HEX64_A",
    "HEX64_B",
    "HEX64_C",
    "HEX64_D",
    "HEX64_E",
    "HEX64_F",
    "NOVEL_ID",
    "OWNER_ID",
    "PROJECT_ID",
    "PROJECT_KEY",
    "PROJECT_NAME",
    "SPACE",
    "build_fixture_snapshot",
    "fixture_asset",
    "fixture_chapter",
    "fixture_citation_hash",
    "fixture_export_asset",
    "fixture_export_citations",
    "fixture_export_revision",
    "fixture_revision",
    "seal_fixture_manifest",
]
