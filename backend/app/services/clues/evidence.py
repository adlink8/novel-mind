"""Bounded cue/later evidence packages for clue semantic judgment.

Packages are fiction-only, cross-chapter capable, and script-owned.
Recall scores live as metadata and never become lifecycle evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

EVIDENCE_PACKAGE_VERSION = "clue-evidence-package.v1"
DEFAULT_EXCERPT_CHARS = 700
MAX_CUE_UNITS = 3
MAX_LATER_UNITS = 8
MAX_LATER_CHAPTERS = 4
MAX_TOTAL_UNITS = MAX_CUE_UNITS + MAX_LATER_UNITS


class ClueEvidenceScopeError(ValueError):
    """Evidence package fails scope, bound, or hash requirements."""


@dataclass(slots=True, frozen=True)
class ClueEvidenceUnit:
    """One primary-text evidence locator owned by scripts, not the LLM."""

    evidence_id: str
    chapter_id: int
    narrative_chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    text: str
    role_hint: Literal["cue", "later", "unknown"] = "unknown"
    hierarchy_node_id: str | None = None
    timeline_event_id: int | None = None

    def narrative_key(self) -> tuple[int, int]:
        return (self.narrative_chapter_number, self.source_start)

    def identity_key(self) -> str:
        return (
            f"{self.evidence_id}:{self.chapter_id}:"
            f"{self.source_start}:{self.source_end}:{self.content_hash}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "chapter_id": self.chapter_id,
            "narrative_chapter_number": self.narrative_chapter_number,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "content_hash": self.content_hash,
            "excerpt": bounded_excerpt(self.text),
            "role_hint": self.role_hint,
            "hierarchy_node_id": self.hierarchy_node_id,
            "timeline_event_id": self.timeline_event_id,
        }


@dataclass(slots=True)
class ClueEvidencePackage:
    """Cross-chapter package: one early cue window + bounded later windows."""

    owner_id: int
    novel_id: int
    candidate_id: str
    source_snapshot_hash: str
    hierarchy_build_id: str
    hierarchy_checksum: str
    timeline_version_id: int | None
    timeline_checksum: str | None
    cue_units: list[ClueEvidenceUnit] = field(default_factory=list)
    later_units: list[ClueEvidenceUnit] = field(default_factory=list)
    recall_signals: dict[str, Any] = field(default_factory=dict)
    omitted_evidence_ids: list[str] = field(default_factory=list)
    package_hash: str = ""
    domain: Literal["fiction"] = "fiction"

    def all_units(self) -> list[ClueEvidenceUnit]:
        return list(self.cue_units) + list(self.later_units)

    def allowed_evidence_ids(self) -> list[str]:
        return [u.evidence_id for u in self.all_units()]

    def unit_by_id(self) -> dict[str, ClueEvidenceUnit]:
        return {u.evidence_id: u for u in self.all_units()}

    def cue_ids(self) -> list[str]:
        return [u.evidence_id for u in self.cue_units]

    def later_ids(self) -> list[str]:
        return [u.evidence_id for u in self.later_units]

    def to_llm_payload(self) -> dict[str, Any]:
        """Bounded payload the model may see — no full novel, no chat history."""

        return {
            "package_version": EVIDENCE_PACKAGE_VERSION,
            "domain": self.domain,
            "candidate_id": self.candidate_id,
            "allowed_classifications": [
                "cue_only",
                "reinforcement",
                "payoff",
                "unrelated",
                "ambiguous",
            ],
            "allowed_conflict_flags": [
                "MOTIF_ONLY",
                "ORDER_CONFLICT",
                "ENTITY_CONFLICT",
                "UNRESOLVED_REFERENCE",
                "INSUFFICIENT_PAYOFF",
            ],
            "allowed_evidence_ids": self.allowed_evidence_ids(),
            "cue_evidence_ids": self.cue_ids(),
            "later_evidence_ids": self.later_ids(),
            "cue_evidence": [u.to_dict() for u in self.cue_units],
            "later_evidence": [u.to_dict() for u in self.later_units],
            "recall_signals": self.recall_signals,
            "omitted_evidence_ids": list(self.omitted_evidence_ids),
            "lineage": {
                "source_snapshot_hash": self.source_snapshot_hash,
                "hierarchy_build_id": self.hierarchy_build_id,
                "hierarchy_checksum": self.hierarchy_checksum,
                "timeline_version_id": self.timeline_version_id,
                "timeline_checksum": self.timeline_checksum,
                "package_hash": self.package_hash,
            },
            "llm_contract": {
                "json_only": True,
                "schema_version": "clue-semantic-judgment.v1",
                "must_cite_only_allowed_evidence_ids": True,
                "cannot_emit_status_version_or_writes": True,
                "novel_text_is_untrusted_data": True,
                "recall_signals_are_not_proof": True,
                "chat_is_not_evidence": True,
            },
        }

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "package_version": EVIDENCE_PACKAGE_VERSION,
            "domain": self.domain,
            "owner_id": self.owner_id,
            "novel_id": self.novel_id,
            "candidate_id": self.candidate_id,
            "source_snapshot_hash": self.source_snapshot_hash,
            "hierarchy_build_id": self.hierarchy_build_id,
            "hierarchy_checksum": self.hierarchy_checksum,
            "timeline_version_id": self.timeline_version_id,
            "timeline_checksum": self.timeline_checksum,
            "cue_units": [u.to_dict() for u in self.cue_units],
            "later_units": [u.to_dict() for u in self.later_units],
            "recall_signals": self.recall_signals,
            "omitted_evidence_ids": list(self.omitted_evidence_ids),
            "package_hash": self.package_hash,
        }


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()


def package_hash_for(payload: dict[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "package_hash"}
    return sha256_json(body)


def bounded_excerpt(content: str, max_chars: int = DEFAULT_EXCERPT_CHARS) -> str:
    normalized = " ".join((content or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3]}..."


def make_clue_evidence_unit(
    *,
    evidence_id: str,
    chapter_id: int,
    narrative_chapter_number: int,
    text: str,
    source_start: int = 0,
    source_end: int | None = None,
    role_hint: Literal["cue", "later", "unknown"] = "unknown",
    hierarchy_node_id: str | None = None,
    timeline_event_id: int | None = None,
    content_hash: str | None = None,
) -> ClueEvidenceUnit:
    body = text or ""
    end = source_end if source_end is not None else max(len(body), 1)
    if chapter_id <= 0 or narrative_chapter_number <= 0:
        raise ClueEvidenceScopeError("chapter identifiers must be positive")
    if end <= source_start or source_start < 0:
        raise ClueEvidenceScopeError(f"invalid evidence offsets for {evidence_id}")
    excerpt_source = body[source_start:end] if body else " "
    if not excerpt_source:
        excerpt_source = body or " "
        end = source_start + max(len(excerpt_source), 1)
    digest = content_hash or sha256_text(excerpt_source)
    if len(digest) != 64:
        raise ClueEvidenceScopeError("content_hash must be 64-char SHA-256 hex")
    return ClueEvidenceUnit(
        evidence_id=evidence_id,
        chapter_id=chapter_id,
        narrative_chapter_number=narrative_chapter_number,
        source_start=source_start,
        source_end=end,
        content_hash=digest,
        text=excerpt_source,
        role_hint=role_hint,
        hierarchy_node_id=hierarchy_node_id,
        timeline_event_id=timeline_event_id,
    )


def build_clue_evidence_package(
    *,
    owner_id: int,
    novel_id: int,
    candidate_id: str,
    source_snapshot_hash: str,
    hierarchy_build_id: str,
    hierarchy_checksum: str,
    cue_units: list[ClueEvidenceUnit],
    later_units: list[ClueEvidenceUnit],
    timeline_version_id: int | None = None,
    timeline_checksum: str | None = None,
    recall_signals: dict[str, Any] | None = None,
    omitted_evidence_ids: list[str] | None = None,
) -> ClueEvidencePackage:
    """Build a bounded cue/later package; never includes the full novel or chat."""

    if owner_id <= 0 or novel_id <= 0:
        raise ClueEvidenceScopeError("owner_id and novel_id must be positive")
    if not candidate_id:
        raise ClueEvidenceScopeError("candidate_id is required")
    if not hierarchy_build_id:
        raise ClueEvidenceScopeError("hierarchy_build_id is required")
    if len(source_snapshot_hash) != 64 or len(hierarchy_checksum) != 64:
        raise ClueEvidenceScopeError("lineage hashes must be 64-char SHA-256 hex")
    if timeline_checksum is not None and len(timeline_checksum) != 64:
        raise ClueEvidenceScopeError("timeline_checksum must be 64-char SHA-256 hex")
    if not cue_units:
        raise ClueEvidenceScopeError("cue window requires at least one evidence unit")
    if len(cue_units) > MAX_CUE_UNITS:
        raise ClueEvidenceScopeError(f"cue window exceeds max {MAX_CUE_UNITS}")
    if len(later_units) > MAX_LATER_UNITS:
        raise ClueEvidenceScopeError(f"later window exceeds max {MAX_LATER_UNITS}")

    later_chapters = {u.narrative_chapter_number for u in later_units}
    if len(later_chapters) > MAX_LATER_CHAPTERS:
        raise ClueEvidenceScopeError(
            f"later windows span more than {MAX_LATER_CHAPTERS} chapters"
        )

    all_ids = [u.evidence_id for u in cue_units] + [u.evidence_id for u in later_units]
    if len(set(all_ids)) != len(all_ids):
        raise ClueEvidenceScopeError("evidence IDs must be unique within a package")
    if any(not eid for eid in all_ids):
        raise ClueEvidenceScopeError("empty evidence_id is forbidden")

    # Sort for deterministic package_hash.
    cue_sorted = sorted(cue_units, key=lambda u: (*u.narrative_key(), u.evidence_id))
    later_sorted = sorted(later_units, key=lambda u: (*u.narrative_key(), u.evidence_id))

    package = ClueEvidencePackage(
        owner_id=owner_id,
        novel_id=novel_id,
        candidate_id=candidate_id,
        source_snapshot_hash=source_snapshot_hash,
        hierarchy_build_id=hierarchy_build_id,
        hierarchy_checksum=hierarchy_checksum,
        timeline_version_id=timeline_version_id,
        timeline_checksum=timeline_checksum,
        cue_units=list(cue_sorted),
        later_units=list(later_sorted),
        recall_signals=dict(recall_signals or {}),
        omitted_evidence_ids=sorted(omitted_evidence_ids or []),
    )
    package.package_hash = package_hash_for(package.to_snapshot())
    return package


def trim_units_deterministically(
    units: list[ClueEvidenceUnit],
    *,
    limit: int,
    scores: dict[str, float] | None = None,
) -> tuple[list[ClueEvidenceUnit], list[str]]:
    """Trim whole units by score, narrative distance, then source order."""

    if len(units) <= limit:
        return list(units), []
    score_map = scores or {}
    ranked = sorted(
        units,
        key=lambda u: (
            -float(score_map.get(u.evidence_id, 0.0)),
            u.narrative_chapter_number,
            u.source_start,
            u.evidence_id,
        ),
    )
    kept = ranked[:limit]
    omitted = [u.evidence_id for u in ranked[limit:]]
    kept_sorted = sorted(kept, key=lambda u: (*u.narrative_key(), u.evidence_id))
    return kept_sorted, omitted


def clamp_later_units_to_scope(
    units: list[ClueEvidenceUnit],
    *,
    max_units: int = MAX_LATER_UNITS,
    max_chapters: int = MAX_LATER_CHAPTERS,
    scores: dict[str, float] | None = None,
    cue_chapter: int | None = None,
) -> tuple[list[ClueEvidenceUnit], list[str]]:
    """Clamp later units to chapter-span and unit bounds before package build.

    Prefer chapters closest to the cue window; break ties by densest score sum,
    then chapter number. Within kept chapters, keep highest-score units up to
    ``max_units``. Omitted evidence ids are returned for lineage metadata.
    """

    if not units:
        return [], []
    if max_units <= 0 or max_chapters <= 0:
        return [], [u.evidence_id for u in units]

    score_map = scores or {}
    by_chapter: dict[int, list[ClueEvidenceUnit]] = {}
    for unit in units:
        by_chapter.setdefault(unit.narrative_chapter_number, []).append(unit)

    def _chapter_key(chapter: int) -> tuple[int, float, int]:
        chapter_units = by_chapter[chapter]
        density = sum(float(score_map.get(u.evidence_id, 0.0)) for u in chapter_units)
        if cue_chapter is None:
            distance = chapter
        else:
            distance = abs(chapter - cue_chapter)
        # Closer first, then denser payoff chapters, then stable chapter order.
        return (distance, -density, chapter)

    selected_chapters = sorted(by_chapter.keys(), key=_chapter_key)[:max_chapters]
    selected_set = set(selected_chapters)

    in_scope: list[ClueEvidenceUnit] = []
    out_of_scope: list[ClueEvidenceUnit] = []
    for unit in units:
        if unit.narrative_chapter_number in selected_set:
            in_scope.append(unit)
        else:
            out_of_scope.append(unit)

    kept, unit_omitted = trim_units_deterministically(
        in_scope,
        limit=max_units,
        scores=score_map,
    )
    omitted = [u.evidence_id for u in out_of_scope] + unit_omitted
    # Stable omitted order for package_hash determinism.
    omitted_sorted = sorted(set(omitted))
    return kept, omitted_sorted


def validate_package_scope(
    package: ClueEvidencePackage,
    *,
    owner_id: int,
    novel_id: int,
    hierarchy_build_id: str | None = None,
) -> list[str]:
    """Return machine-readable scope failures (empty means pass)."""

    failures: list[str] = []
    if package.owner_id != owner_id:
        failures.append("scope:owner_mismatch")
    if package.novel_id != novel_id:
        failures.append("scope:novel_mismatch")
    if hierarchy_build_id is not None and package.hierarchy_build_id != hierarchy_build_id:
        failures.append("scope:hierarchy_build_mismatch")
    if package.domain != "fiction":
        failures.append("scope:non_fiction")
    return failures
