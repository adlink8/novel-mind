"""Visual Bible evidence materialization (Phase 30-02, REQ-VIS-01).

D-30-01..D-30-04: every visual claim is evidence-linked, canon/interpretation
labels stay distinct, reusable stable IDs are scoped by owner+novel+version,
and approval is an explicit append-only human action.

This module owns the *evidence seam*:

- ``VisualBibleEvidenceService.materialize_version_claims`` re-reads the source
  snapshot leaf evidence directly from the owning novel's ``Chapter`` rows
  (fresh DB authority) and re-verifies, for every canon_fact claim:

    - owner/novel scope (the novel must belong to the requesting owner),
    - the version's ``source_snapshot_hash`` against a deterministic hash of the
      novel's current chapter set (stale snapshot lineage fails closed),
    - chapter presence inside the novel, chapter-number integrity,
    - the spoiler cutoff (``chapter_number <= cutoff_chapter``),
    - Unicode offset bounds and the exact slice content hash.

  A claim that cannot be fully verified is returned as a reason-coded
  ``ClaimUnresolved`` (missing evidence / stale hash / beyond cutoff / offset /
  slice hash / scope). Nothing unresolved can ever be promoted to a candidate.

- The materialized slice is exactly what the version claims reference; the
  authoritative chapter text is never rewritten by this module (D-30-01/02).

No image provider is called (Phase 32-33) and no row is mutated here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.novel import Chapter, Novel
from app.schemas.visual_bible import (
    VisualAuthority,
    VisualBibleGateError,
    VisualClaimContract,
    VisualEvidenceRef,
    canonical_visual_hash,
    validate_claim_evidence,
    validate_claim_hash,
    validate_evidence_against_source,
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
    text still have distinct snapshot lineage. Replay recomputes it and any
    chapter drift fails closed (stale snapshot lineage).
    """
    records = [
        {
            "chapter_number": record.chapter_number,
            "content_hash": chapter_content_hash(record.content),
        }
        for record in sorted(chapters, key=lambda c: c.chapter_number)
    ]
    return canonical_visual_hash(
        {
            "kind": "visual_bible.source_snapshot",
            "owner_id": owner_id,
            "novel_id": novel_id,
            "chapters": records,
        }
    )


@dataclass(frozen=True)
class MaterializedClaim:
    """A claim whose canon evidence was re-verified against the DB source."""

    claim: VisualClaimContract
    verified_evidence: tuple[VisualEvidenceRef, ...]


@dataclass(frozen=True)
class ClaimUnresolved:
    """A claim that cannot be materialized; stable machine reason code."""

    claim_key: str
    reason_code: str
    detail: str


@dataclass(frozen=True)
class MaterializeOutcome:
    """Batch result: resolved claims + reason-coded unresolved claims."""

    resolved: tuple[MaterializedClaim, ...]
    unresolved: tuple[ClaimUnresolved, ...]

    @property
    def blocked(self) -> bool:
        return bool(self.unresolved)


class VisualBibleEvidenceService:
    """Owner/novel-scoped evidence seam; reads the novel as fresh authority."""

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

    async def materialize_version_claims(
        self,
        *,
        owner_id: int,
        novel_id: int,
        source_snapshot_id: str,
        source_snapshot_hash: str,
        cutoff_chapter: int,
        claims: Sequence[VisualClaimContract],
    ) -> MaterializeOutcome:
        """Re-verify every claim's evidence against the owned novel source.

        Non-canon claims (interpretation labels) are carried as resolved with
        empty verified evidence; only canon_fact claims require re-sliced leaf
        evidence. Any verification failure is reason-coded unresolved and never
        promotes anything.
        """
        novel = await self.verify_novel_scope(owner_id=owner_id, novel_id=novel_id)
        if novel is None:
            return MaterializeOutcome(
                resolved=(),
                unresolved=tuple(
                    ClaimUnresolved(
                        claim_key=claim.claim_key,
                        reason_code="owner_scope_mismatch",
                        detail="owner does not own the novel for this visual bible",
                    )
                    for claim in claims
                ),
            )

        current_hash, chapters = await self.load_source_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        by_chapter_id = {record.chapter_id: record for record in chapters}

        resolved: list[MaterializedClaim] = []
        unresolved: list[ClaimUnresolved] = []
        for claim in claims:
            result = self._materialize_one(
                claim=claim,
                by_chapter_id=by_chapter_id,
                current_snapshot_hash=current_hash,
                source_snapshot_id=source_snapshot_id,
                source_snapshot_hash=source_snapshot_hash,
                cutoff_chapter=cutoff_chapter,
            )
            if isinstance(result, MaterializedClaim):
                resolved.append(result)
            else:
                unresolved.append(result)
        return MaterializeOutcome(
            resolved=tuple(resolved),
            unresolved=tuple(unresolved),
        )

    # ------------------------------------------------------------------ per-claim

    def _materialize_one(
        self,
        *,
        claim: VisualClaimContract,
        by_chapter_id: dict[int, ChapterRecord],
        current_snapshot_hash: str,
        source_snapshot_id: str,
        source_snapshot_hash: str,
        cutoff_chapter: int,
    ) -> MaterializedClaim | ClaimUnresolved:
        try:
            validate_claim_hash(claim)
        except VisualBibleGateError as exc:
            return ClaimUnresolved(
                claim.claim_key, "claim_hash_mismatch", str(exc)
            )

        # Interpretation labels carry author + rationale, never leaf evidence.
        if claim.authority is not VisualAuthority.CANON_FACT:
            return MaterializedClaim(claim=claim, verified_evidence=())

        if source_snapshot_hash != current_snapshot_hash:
            return ClaimUnresolved(
                claim.claim_key,
                "stale_snapshot_lineage",
                "source snapshot hash does not match the novel's current chapter set",
            )

        try:
            validate_claim_evidence(
                claim,
                source_snapshot_id=source_snapshot_id,
                source_snapshot_hash=source_snapshot_hash,
                cutoff_chapter=cutoff_chapter,
            )
        except VisualBibleGateError as exc:
            return ClaimUnresolved(
                claim.claim_key, "evidence_lineage_mismatch", str(exc)
            )

        verified: list[VisualEvidenceRef] = []
        for ref in claim.evidence_refs:
            chapter = by_chapter_id.get(ref.chapter_id)
            if chapter is None:
                return ClaimUnresolved(
                    claim.claim_key,
                    "chapter_missing",
                    f"chapter {ref.chapter_id} is absent from the owning novel",
                )
            if chapter.chapter_number != ref.chapter_number:
                return ClaimUnresolved(
                    claim.claim_key,
                    "chapter_number_mismatch",
                    f"chapter {ref.chapter_id} is numbered {chapter.chapter_number}, "
                    f"evidence expects {ref.chapter_number}",
                )
            if chapter.chapter_number > cutoff_chapter:
                return ClaimUnresolved(
                    claim.claim_key,
                    "beyond_cutoff",
                    f"evidence chapter {chapter.chapter_number} exceeds the spoiler "
                    f"cutoff {cutoff_chapter}",
                )
            try:
                validate_evidence_against_source(chapter.content, ref)
            except VisualBibleGateError as exc:
                return ClaimUnresolved(
                    claim.claim_key, "evidence_content_mismatch", str(exc)
                )
            verified.append(ref)

        return MaterializedClaim(
            claim=claim, verified_evidence=tuple(verified)
        )
