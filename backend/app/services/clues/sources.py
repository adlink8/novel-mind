"""Read-only Phase 09/selection source protocols for clue recall.

Phase 09 relationship observations may contribute *recall signals only*.
Outages are recorded as ``source_unavailable`` and must not be rewritten as
empty-success (zero-signal) results.

Phase 10 chat free-form text is never a clue fact source. Only primary
selection/citation references may be accepted as locators; free-form messages
are rejected. This module does not implement chat business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol, runtime_checkable

RelationshipSourceStatus = Literal["ok", "source_unavailable", "empty"]
CitationSourceStatus = Literal["ok", "rejected", "empty"]


@dataclass(slots=True, frozen=True)
class RelationshipObservationRef:
    """Versioned observation locator; never promoted to lifecycle evidence alone."""

    observation_ref: str
    analysis_version_id: int
    source_character_id: int
    target_character_id: int
    relation_type: str
    valid_from_chapter: int
    evidence_ids: tuple[str, ...] = ()
    reason_code: str = "relationship_observation"


@dataclass(slots=True)
class RelationshipSourceResult:
    """Explicit status for Phase 09 reader outcomes.

    - ``ok``: reader responded with zero or more observations
    - ``empty``: reader responded successfully with zero observations
    - ``source_unavailable``: reader missing, unbound, or runtime failure
    """

    status: RelationshipSourceStatus
    items: list[RelationshipObservationRef] = field(default_factory=list)
    reason_code: str = ""
    detail: str | None = None

    @property
    def is_unavailable(self) -> bool:
        return self.status == "source_unavailable"

    def recall_signals(self) -> dict[str, Any]:
        """Signals only — never accepted lifecycle state."""

        if self.status == "source_unavailable":
            return {
                "relationship": {
                    "status": "source_unavailable",
                    "reason_code": self.reason_code or "source_unavailable",
                    "detail": self.detail,
                    "count": 0,
                }
            }
        if not self.items:
            return {
                "relationship": {
                    "status": "empty" if self.status == "empty" else "ok",
                    "reason_code": self.reason_code or "no_observations",
                    "count": 0,
                }
            }
        return {
            "relationship": {
                "status": "ok",
                "reason_code": self.reason_code or "observations_present",
                "count": len(self.items),
                "refs": [
                    {
                        "observation_ref": item.observation_ref,
                        "analysis_version_id": item.analysis_version_id,
                        "relation_type": item.relation_type,
                        "valid_from_chapter": item.valid_from_chapter,
                        "evidence_ids": list(item.evidence_ids),
                    }
                    for item in self.items
                ],
            }
        }


@runtime_checkable
class VersionedRelationshipObservationSource(Protocol):
    """Read-only protocol bound to a completed Phase 09 public reader only."""

    async def list_observations(
        self,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int | None = None,
        through_chapter: int | None = None,
    ) -> RelationshipSourceResult:
        """Return version-scoped observations or an explicit unavailable result."""
        ...


class NullRelationshipObservationSource:
    """Default when Phase 09 public reader is not bound."""

    async def list_observations(
        self,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int | None = None,
        through_chapter: int | None = None,
    ) -> RelationshipSourceResult:
        return RelationshipSourceResult(
            status="source_unavailable",
            items=[],
            reason_code="source_unavailable",
            detail="phase09_public_reader_not_bound",
        )


class UnavailableRelationshipObservationSource:
    """Explicit outage recorder (tests / fail-closed path)."""

    def __init__(self, detail: str = "phase09_runtime_outage") -> None:
        self.detail = detail

    async def list_observations(
        self,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int | None = None,
        through_chapter: int | None = None,
    ) -> RelationshipSourceResult:
        return RelationshipSourceResult(
            status="source_unavailable",
            items=[],
            reason_code="source_unavailable",
            detail=self.detail,
        )


class StaticRelationshipObservationSource:
    """In-memory source for deterministic tests (simulates healthy Phase 09)."""

    def __init__(self, items: list[RelationshipObservationRef] | None = None) -> None:
        self._items = list(items or [])

    async def list_observations(
        self,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int | None = None,
        through_chapter: int | None = None,
    ) -> RelationshipSourceResult:
        filtered: list[RelationshipObservationRef] = []
        for item in self._items:
            if analysis_version_id is not None and item.analysis_version_id != analysis_version_id:
                continue
            if through_chapter is not None and item.valid_from_chapter > through_chapter:
                continue
            filtered.append(item)
        if not filtered:
            return RelationshipSourceResult(
                status="empty",
                items=[],
                reason_code="no_observations",
            )
        return RelationshipSourceResult(
            status="ok",
            items=filtered,
            reason_code="observations_present",
        )


# Callable matching a minimal Phase 09 public reader surface.
Phase09ReaderCallable = Callable[..., Awaitable[list[Any]]]


class Phase09BoundRelationshipSource:
    """Bind only to a completed Phase 09 public reader; never invent rows."""

    def __init__(self, reader: Phase09ReaderCallable | None) -> None:
        self._reader = reader

    async def list_observations(
        self,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int | None = None,
        through_chapter: int | None = None,
    ) -> RelationshipSourceResult:
        if self._reader is None:
            return await NullRelationshipObservationSource().list_observations(
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=analysis_version_id,
                through_chapter=through_chapter,
            )
        try:
            raw = await self._reader(
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=analysis_version_id,
                through_chapter=through_chapter,
            )
        except Exception as exc:  # noqa: BLE001 — explicit outage contract
            return RelationshipSourceResult(
                status="source_unavailable",
                items=[],
                reason_code="source_unavailable",
                detail=f"phase09_reader_error:{type(exc).__name__}",
            )

        items: list[RelationshipObservationRef] = []
        for row in raw or []:
            try:
                items.append(_coerce_observation_ref(row))
            except (TypeError, ValueError, AttributeError):
                continue
        if not items:
            return RelationshipSourceResult(
                status="empty",
                items=[],
                reason_code="no_observations",
            )
        return RelationshipSourceResult(
            status="ok",
            items=items,
            reason_code="observations_present",
        )


def _coerce_observation_ref(row: Any) -> RelationshipObservationRef:
    if isinstance(row, RelationshipObservationRef):
        return row
    if isinstance(row, dict):
        return RelationshipObservationRef(
            observation_ref=str(row["observation_ref"]),
            analysis_version_id=int(row["analysis_version_id"]),
            source_character_id=int(row["source_character_id"]),
            target_character_id=int(row["target_character_id"]),
            relation_type=str(row["relation_type"]),
            valid_from_chapter=int(row["valid_from_chapter"]),
            evidence_ids=tuple(str(x) for x in row.get("evidence_ids") or ()),
            reason_code=str(row.get("reason_code") or "relationship_observation"),
        )
    return RelationshipObservationRef(
        observation_ref=str(getattr(row, "observation_ref")),
        analysis_version_id=int(getattr(row, "analysis_version_id")),
        source_character_id=int(getattr(row, "source_character_id")),
        target_character_id=int(getattr(row, "target_character_id")),
        relation_type=str(getattr(row, "relation_type")),
        valid_from_chapter=int(getattr(row, "valid_from_chapter")),
        evidence_ids=tuple(str(x) for x in getattr(row, "evidence_ids", ()) or ()),
        reason_code=str(getattr(row, "reason_code", "relationship_observation")),
    )


# ---------------------------------------------------------------------------
# Selection / citation refs (not free-form chat)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class PrimarySelectionCitationRef:
    """Primary text selection or citation locator — never free-form chat text."""

    ref_id: str
    chapter_id: int
    source_start: int
    source_end: int
    content_hash: str
    kind: Literal["selection", "citation"]
    excerpt: str | None = None


@dataclass(slots=True)
class CitationSourceResult:
    status: CitationSourceStatus
    items: list[PrimarySelectionCitationRef] = field(default_factory=list)
    reason_code: str = ""
    detail: str | None = None


def accept_primary_selection_citation_refs(
    payloads: list[dict[str, Any]] | list[Any],
) -> CitationSourceResult:
    """Accept only structured selection/citation refs; reject free-form chat."""

    accepted: list[PrimarySelectionCitationRef] = []
    for raw in payloads or []:
        try:
            accepted.append(_coerce_selection_citation(raw))
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            return CitationSourceResult(
                status="rejected",
                items=[],
                reason_code="freeform_or_malformed_rejected",
                detail=str(exc),
            )
    if not accepted:
        return CitationSourceResult(
            status="empty",
            items=[],
            reason_code="no_citations",
        )
    return CitationSourceResult(
        status="ok",
        items=accepted,
        reason_code="selection_citation_ok",
    )


def reject_freeform_chat_as_evidence(message_text: str | None) -> CitationSourceResult:
    """Hard reject free-form conversation messages as clue evidence."""

    return CitationSourceResult(
        status="rejected",
        items=[],
        reason_code="chat_freeform_forbidden",
        detail="phase10_chat_is_not_clue_fact_source",
    )


def _coerce_selection_citation(raw: Any) -> PrimarySelectionCitationRef:
    if isinstance(raw, PrimarySelectionCitationRef):
        return raw
    data = raw if isinstance(raw, dict) else {
        "ref_id": getattr(raw, "ref_id"),
        "chapter_id": getattr(raw, "chapter_id"),
        "source_start": getattr(raw, "source_start"),
        "source_end": getattr(raw, "source_end"),
        "content_hash": getattr(raw, "content_hash"),
        "kind": getattr(raw, "kind"),
        "excerpt": getattr(raw, "excerpt", None),
    }
    # Free-form chat smuggling markers.
    if "message_text" in data or "chat_text" in data or "conversation_id" in data:
        raise ValueError("freeform chat fields are forbidden")
    kind = str(data.get("kind") or "")
    if kind not in {"selection", "citation"}:
        raise ValueError(f"unsupported citation kind: {kind!r}")
    chapter_id = int(data["chapter_id"])
    start = int(data["source_start"])
    end = int(data["source_end"])
    content_hash = str(data["content_hash"])
    ref_id = str(data["ref_id"])
    if not ref_id or chapter_id <= 0 or end <= start or start < 0:
        raise ValueError("invalid selection/citation locator")
    if len(content_hash) != 64:
        raise ValueError("content_hash must be 64-char SHA-256 hex")
    # Reject payloads that only carry free prose without offsets (already checked).
    return PrimarySelectionCitationRef(
        ref_id=ref_id,
        chapter_id=chapter_id,
        source_start=start,
        source_end=end,
        content_hash=content_hash,
        kind=kind,  # type: ignore[arg-type]
        excerpt=data.get("excerpt"),
    )
