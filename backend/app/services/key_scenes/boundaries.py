"""Scene boundary detection over the persisted chapter hierarchy (Phase 31-01).

REQ-VIS-02 / D-31-01..D-31-05: scene candidates are evidence-first derived
artifacts. This module owns the *boundary seam*:

- ``SceneBoundaryService.detect_chapter_boundaries`` reuses the persisted
  chapter → scene → evidence hierarchy (``app/services/chunking``) so every
  candidate locates to a replayable source range. Scene boundaries carry
  chapter/range/source hash and the owning snapshot; malformed/ambiguous
  boundaries surface as stable reason codes (``no_scene_boundaries`` /
  ``malformed_range``) instead of silent degradation.
- ``detect_dialogue_heuristic`` is a deterministic textual heuristic
  (REQ-VIS-06). It returns a typed ``SpeakerDialogueHeuristicSignal`` whose
  source-relative ``speaker_offsets``/``dialogue_offsets``/``confidence``/
  ``warnings`` are advisory candidate metadata only. Missing or ambiguous
  attribution is explicitly ``unavailable``/``ambiguous`` with warnings — it is
  never silently promoted to a score, evidence, citation or approval reason.
- ``SceneBoundaryService`` owns the owner/novel-scoped DB seams:
  ``verify_novel_scope``, ``load_source_snapshot`` and
  ``verify_visual_bible_approval`` (the approved Visual Bible revision's owner,
  version, approved status and manifest hash are re-verified before a set can
  cite it). No source text and no active reader state is ever rewritten here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.novel import Chapter, Novel
from app.models.visual_bible import VisualBibleVersion
from app.schemas.key_scene import (
    DialogueOffset,
    HeuristicSignalAvailability,
    SceneCandidateContract,
    SceneCoordinates,
    SceneEvidenceRange,
    SalienceReason,
    SpeakerDialogueHeuristicSignal,
    SpeakerOffset,
    canonical_key_scene_hash,
)
from app.services.chunking.hierarchy import build_chapter_hierarchy

# Attribute phrases that indicate a speaker name before/after a quoted span.
# Advisory only: this never becomes a factual claim or citation authority.
_SPEAKER_BEFORE = re.compile(
    r"([\w\u4e00-\u9fff]{1,30})\s*(?:说道|说|道|问道|答道|said|asked|喊|叫)\s*[：:]?\s*$"
)
_SPEAKER_AFTER = re.compile(
    r"^\s*([\w\u4e00-\u9fff]{1,30})\s*(?:说道|说|道|问道|答道|said|asked|喊|叫)"
)
_QUOTE_PAIRS = (
    ('"', '"'),
    ("\u201c", "\u201d"),
    ("\u2018", "\u2019"),
    ("\u300c", "\u300d"),  # 「」
    ("\u300e", "\u300f"),  # 『』
)


@dataclass(frozen=True)
class ChapterRecord:
    """One frozen chapter text record loaded fresh from the owning novel."""

    chapter_id: int
    chapter_number: int
    content: str


def chapter_content_hash(content: str) -> str:
    """Deterministic 64-hex content hash of one chapter body."""
    return sha256(content.encode("utf-8")).hexdigest()


def compute_source_snapshot_hash(
    *, owner_id: int, novel_id: int, chapters: Sequence[ChapterRecord]
) -> str:
    """Deterministic content address of a novel's current chapter set.

    The address binds owner/novel scope so two novels with identical chapter
    text still have distinct snapshot lineage, and replay recomputes it so any
    chapter drift fails closed (stale snapshot lineage).
    """
    records = [
        {
            "chapter_number": record.chapter_number,
            "content_hash": chapter_content_hash(record.content),
        }
        for record in sorted(chapters, key=lambda c: c.chapter_number)
    ]
    return canonical_key_scene_hash(
        {
            "kind": "key_scene.source_snapshot",
            "owner_id": owner_id,
            "novel_id": novel_id,
            "chapters": records,
        }
    )


# ---------------------------------------------------------------------------
# Pure boundary detection (reuses the chapter → scene → evidence hierarchy)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneBoundary:
    """One deterministic scene boundary from the persisted hierarchy."""

    scene_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    source_hash: str
    source_snapshot_hash: str | None
    content: str
    order_index: int


@dataclass(frozen=True)
class BoundaryIssue:
    """A malformed/ambiguous boundary; stable machine reason code."""

    chapter_id: int
    reason_code: str
    detail: str


@dataclass(frozen=True)
class BoundaryOutcome:
    """Detector result: boundaries + detector reason codes + malformed issues."""

    boundaries: tuple[SceneBoundary, ...]
    reason_codes: tuple[str, ...]
    malformed: tuple[BoundaryIssue, ...] = ()

    @property
    def blocked(self) -> bool:
        return bool(self.malformed) and not self.boundaries


def detect_chapter_boundaries(
    *,
    novel_id: int,
    chapter_id: int,
    chapter_number: int,
    content: str,
    source_snapshot_hash: str | None = None,
) -> BoundaryOutcome:
    """Deterministic scene boundary detection over one chapter's source slice.

    Scene nodes come from the persisted chunk hierarchy so candidate ranges are
    the same boundaries the source index uses. Every boundary is verified
    against the chapter source slice (``content[start:end] == scene content``);
    drift surfaces as ``malformed_range`` issues, never as silent output.
    """
    content = content or ""
    if not content.strip():
        return BoundaryOutcome(
            boundaries=(),
            reason_codes=("no_scene_boundaries",),
            malformed=(
                BoundaryIssue(
                    chapter_id=chapter_id,
                    reason_code="no_scene_boundaries",
                    detail="chapter body is empty",
                ),
            ),
        )

    tree = build_chapter_hierarchy(
        novel_id=novel_id,
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        content=content,
        source_snapshot_hash=source_snapshot_hash,
    )
    scenes = sorted(
        (node for node in tree.nodes if node.level == "scene"),
        key=lambda node: node.order_index,
    )

    boundaries: list[SceneBoundary] = []
    issues: list[BoundaryIssue] = []
    for scene in scenes:
        if scene.source_end <= scene.source_start:
            issues.append(
                BoundaryIssue(
                    chapter_id=chapter_id,
                    reason_code="malformed_range",
                    detail=f"scene {scene.node_id} has a non-positive source range",
                )
            )
            continue
        if content[scene.source_start : scene.source_end] != scene.content:
            issues.append(
                BoundaryIssue(
                    chapter_id=chapter_id,
                    reason_code="malformed_range",
                    detail=(
                        f"scene {scene.node_id} slice does not match the "
                        "persisted chapter source"
                    ),
                )
            )
            continue
        boundaries.append(
            SceneBoundary(
                scene_id=scene.node_id,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                source_start=scene.source_start,
                source_end=scene.source_end,
                source_hash=scene.content_hash,
                source_snapshot_hash=tree.source_snapshot_hash or source_snapshot_hash,
                content=scene.content,
                order_index=scene.order_index,
            )
        )

    reason_codes: list[str] = []
    if boundaries:
        reason_codes.append("evidence_boundary")
    else:
        reason_codes.append("no_scene_boundaries")
    if issues:
        reason_codes.append("malformed_range")
    return BoundaryOutcome(
        boundaries=tuple(boundaries),
        reason_codes=tuple(reason_codes),
        malformed=tuple(issues),
    )


def build_evidence_range(
    boundary: SceneBoundary,
    *,
    source_snapshot_id: str,
    source_snapshot_hash: str,
    cutoff_chapter: int,
    evidence_key: str,
) -> SceneEvidenceRange:
    """One source-linked evidence range for a boundary (the citation authority)."""
    return SceneEvidenceRange(
        evidence_key=evidence_key,
        source_snapshot_id=source_snapshot_id,
        source_snapshot_hash=source_snapshot_hash,
        chapter_id=boundary.chapter_id,
        chapter_number=boundary.chapter_number,
        source_start=boundary.source_start,
        source_end=boundary.source_end,
        content_hash=boundary.source_hash,
        excerpt=boundary.content[:300],
        cutoff_chapter=cutoff_chapter,
    )


@dataclass(frozen=True)
class CutoffFilterOutcome:
    """Spoiler-safe boundary filter result."""

    kept: tuple[SceneBoundary, ...]
    excluded: tuple[SceneBoundary, ...]
    reason_codes: tuple[str, ...] = ()


def filter_by_cutoff(
    boundaries: Sequence[SceneBoundary], *, cutoff_chapter: int
) -> CutoffFilterOutcome:
    """Server-side spoiler cutoff: future-chapter candidates never survive."""
    kept = [b for b in boundaries if b.chapter_number <= cutoff_chapter]
    excluded = [b for b in boundaries if b.chapter_number > cutoff_chapter]
    return CutoffFilterOutcome(
        kept=tuple(kept),
        excluded=tuple(excluded),
        reason_codes=("beyond_cutoff",) if excluded else (),
    )


def build_candidate(
    *,
    candidate_key: str,
    candidate_order: int,
    boundary: SceneBoundary,
    coordinates: SceneCoordinates,
    salience_reasons: list[SalienceReason],
    score_total: float,
    score_breakdown: dict,
    diversity_key: str,
    detector_id: str,
    detector_version: str,
    policy_hash: str,
    evidence_range: SceneEvidenceRange,
    heuristic_signal: SpeakerDialogueHeuristicSignal | None = None,
) -> SceneCandidateContract:
    """Assemble one strict candidate contract from a verified boundary.

    The evidence range is the only citation authority the candidate carries; a
    heuristic signal (if any) stays separate diagnostic metadata (D-31-05).
    """
    return SceneCandidateContract(
        candidate_key=candidate_key,
        candidate_order=candidate_order,
        scene_id=boundary.scene_id,
        chapter_id=boundary.chapter_id,
        chapter_number=boundary.chapter_number,
        source_start=boundary.source_start,
        source_end=boundary.source_end,
        source_hash=boundary.source_hash,
        coordinates=coordinates,
        spoiler_cutoff=evidence_range.cutoff_chapter,
        salience_reasons=salience_reasons,
        score_total=score_total,
        score_breakdown=score_breakdown,
        diversity_key=diversity_key,
        detector_id=detector_id,
        detector_version=detector_version,
        policy_hash=policy_hash,
        evidence_ranges=[evidence_range],
        heuristic_signal=heuristic_signal,
    )


# ---------------------------------------------------------------------------
# REQ-VIS-06 advisory speaker/dialogue textual heuristic (deterministic)
# ---------------------------------------------------------------------------


def _find_quoted_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) of quoted dialogue spans; pairs only."""
    spans: list[tuple[int, int]] = []
    for open_ch, close_ch in _QUOTE_PAIRS:
        start: int | None = None
        for i, char in enumerate(text):
            if start is None and char == open_ch:
                start = i + 1
            elif start is not None and char == close_ch:
                if i > start:
                    spans.append((start, i))
                start = None
    return sorted(spans)


def _find_speaker_before(text: str, quote_start: int) -> tuple[int, int, str] | None:
    """Advisory speaker attribution from the text before a quoted span."""
    window = text[max(0, quote_start - 80) : quote_start]
    match = _SPEAKER_BEFORE.search(window)
    if match is None:
        return None
    return (
        max(0, quote_start - 80) + match.start(),
        max(0, quote_start - 80) + match.end(),
        match.group(1),
    )


def _find_speaker_after(text: str, quote_content_end: int) -> tuple[int, int, str] | None:
    """Advisory speaker attribution from the text after a quoted span.

    ``quote_content_end`` is the index of the closing quote character; the
    attribution window starts just after it.
    """
    start = quote_content_end + 1
    window = text[start : start + 80]
    match = _SPEAKER_AFTER.match(window)
    if match is None:
        return None
    return start + match.start(), start + match.end(), match.group(1)


def detect_dialogue_heuristic(
    text: str, *, detector_id: str, detector_version: str
) -> SpeakerDialogueHeuristicSignal:
    """Deterministic textual speaker/dialogue heuristic (advisory, REQ-VIS-06).

    - ``unavailable``: no dialogue spans; no offsets, ``confidence=None`` and a
      warning — never silently treated as a zero-score signal.
    - ``ambiguous``: dialogue exists but attribution is incomplete; reduced
      confidence plus explicit warnings.
    - ``available``: every span has an attribution; full confidence.
    """
    spans = _find_quoted_spans(text)
    if not spans:
        return SpeakerDialogueHeuristicSignal(
            availability=HeuristicSignalAvailability.UNAVAILABLE,
            speaker_offsets=[],
            dialogue_offsets=[],
            confidence=None,
            warnings=["no_dialogue_detected"],
            detector_id=detector_id,
            detector_version=detector_version,
        )

    dialogue_offsets = [
        DialogueOffset(offset_start=start, offset_end=end) for start, end in spans
    ]
    speaker_offsets: list[SpeakerOffset] = []
    warnings: list[str] = []
    attributed = 0
    for start, end in spans:
        found = _find_speaker_before(text, start) or _find_speaker_after(text, end)
        if found is None:
            warnings.append(f"unattributed_dialogue_at_{start}")
            continue
        attributed += 1
        speaker_offsets.append(
            SpeakerOffset(
                offset_start=found[0],
                offset_end=found[1],
                speaker_key=found[2],
            )
        )

    if attributed == len(spans):
        availability = HeuristicSignalAvailability.AVAILABLE
        confidence = 0.9
    elif attributed == 0:
        availability = HeuristicSignalAvailability.AMBIGUOUS
        confidence = 0.3
        warnings.append("no_speaker_attribution")
    else:
        availability = HeuristicSignalAvailability.AMBIGUOUS
        confidence = 0.6
        warnings.append("partial_speaker_attribution")

    return SpeakerDialogueHeuristicSignal(
        availability=availability,
        speaker_offsets=speaker_offsets,
        dialogue_offsets=dialogue_offsets,
        confidence=confidence,
        warnings=warnings,
        detector_id=detector_id,
        detector_version=detector_version,
    )


# ---------------------------------------------------------------------------
# Owner/novel-scoped DB seams (server-side authority)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VisualBibleApprovalCheck:
    """Fail-closed check of the approved Visual Bible revision a set cites."""

    ok: bool
    reason_code: str | None = None
    detail: str | None = None


class SceneBoundaryService:
    """Owner-scoped boundary seam; reads the novel as fresh authority."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def verify_novel_scope(self, *, owner_id: int, novel_id: int) -> Novel | None:
        """The owning novel must belong to the requesting owner (V4 access)."""
        return await self._session.scalar(
            select(Novel).where(
                Novel.id == novel_id,
                Novel.owner_id == owner_id,
            )
        )

    async def load_source_snapshot(
        self, *, owner_id: int, novel_id: int
    ) -> tuple[str, tuple[ChapterRecord, ...]]:
        """Load all chapter bodies of the owned novel and hash the snapshot."""
        rows = (
            await self._session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(
                    Chapter.novel_id == novel_id,
                )
                .order_by(Chapter.chapter_number.asc())
            )
        ).all()
        chapters = tuple(
            ChapterRecord(
                chapter_id=row.id,
                chapter_number=row.chapter_number,
                content=row.content or "",
            )
            for row in rows
        )
        snapshot_hash = compute_source_snapshot_hash(
            owner_id=owner_id,
            novel_id=novel_id,
            chapters=chapters,
        )
        return snapshot_hash, chapters

    async def verify_visual_bible_approval(
        self,
        *,
        owner_id: int,
        novel_id: int,
        approved_visual_bible_revision_id: int | None,
        approved_visual_bible_revision_hash: str | None,
    ) -> VisualBibleApprovalCheck:
        """Re-verify the approved Visual Bible revision a set cites (D-31-01).

        A set with no cited revision is vacuously fine; a cited revision must
        belong to the owner/novel scope, be in the ``approved`` review state and
        match the frozen manifest hash. Any drift fails closed with a stable
        reason code before the set can reference it.
        """
        if approved_visual_bible_revision_id is None and approved_visual_bible_revision_hash is None:
            return VisualBibleApprovalCheck(ok=True)
        if (approved_visual_bible_revision_id is None) != (
            approved_visual_bible_revision_hash is None
        ):
            return VisualBibleApprovalCheck(
                ok=False,
                reason_code="approval_lineage_mismatch",
                detail=(
                    "approved_visual_bible_revision_id and "
                    "approved_visual_bible_revision_hash must be provided together"
                ),
            )

        version = await self._session.scalar(
            select(VisualBibleVersion).where(
                VisualBibleVersion.owner_id == owner_id,
                VisualBibleVersion.novel_id == novel_id,
                VisualBibleVersion.id == approved_visual_bible_revision_id,
            )
        )
        if version is None:
            return VisualBibleApprovalCheck(
                ok=False,
                reason_code="visual_bible_scope_mismatch",
                detail=(
                    "approved visual bible revision is not in the explicit "
                    "owner/novel scope"
                ),
            )
        if version.review_state != "approved":
            return VisualBibleApprovalCheck(
                ok=False,
                reason_code="visual_bible_not_approved",
                detail=(
                    f"visual bible revision review_state is "
                    f"{version.review_state!r}, not 'approved'"
                ),
            )
        if version.manifest_hash != approved_visual_bible_revision_hash:
            return VisualBibleApprovalCheck(
                ok=False,
                reason_code="visual_bible_hash_mismatch",
                detail=(
                    "visual bible revision manifest_hash does not match the "
                    "approved_visual_bible_revision_hash"
                ),
            )
        return VisualBibleApprovalCheck(ok=True)
