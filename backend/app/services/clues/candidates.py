"""Deterministic cross-chapter candidate recall for clue tracking.

Recall combines lexical, vector, adjacency, entity, timeline and optional
relationship reason codes. Scores and source statuses are *signals only* —
they never produce accepted lifecycle states by themselves.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.clues.evidence import (
    ClueEvidencePackage,
    ClueEvidenceScopeError,
    ClueEvidenceUnit,
    build_clue_evidence_package,
    clamp_later_units_to_scope,
    make_clue_evidence_unit,
    sha256_json,
    trim_units_deterministically,
    MAX_CUE_UNITS,
    MAX_LATER_CHAPTERS,
    MAX_LATER_UNITS,
)
from app.services.clues.sources import (
    NullRelationshipObservationSource,
    RelationshipSourceResult,
    VersionedRelationshipObservationSource,
)

logger = logging.getLogger(__name__)

CANDIDATE_ID_PREFIX = "clue-cand"


@dataclass(slots=True)
class HierarchyEvidenceNode:
    """Normalized Phase 07 evidence-level node used for deterministic recall."""

    node_id: str
    chapter_id: int
    narrative_chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    content: str
    level: str = "evidence"
    entities: tuple[str, ...] = ()
    order_index: int = 0


@dataclass(slots=True)
class TimelineEventRef:
    """Optional Phase 08 event anchor used only as a recall signal."""

    event_id: int
    chapter_id: int
    narrative_chapter_number: int
    source_start: int
    title: str = ""
    participant_names: tuple[str, ...] = ()


@dataclass(slots=True)
class ClueCandidateDraft:
    """Potential clue from recall signals — never an accepted lifecycle state."""

    candidate_id: str
    owner_id: int
    novel_id: int
    package: ClueEvidencePackage
    recall_signals: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)

    @property
    def package_hash(self) -> str:
        return self.package.package_hash


@dataclass(slots=True)
class CandidateRecallConfig:
    max_candidates: int = 32
    max_cue_units: int = MAX_CUE_UNITS
    max_later_units: int = MAX_LATER_UNITS
    max_later_chapters: int = MAX_LATER_CHAPTERS
    min_chapter_gap: int = 1
    adjacency_chapter_window: int = 8
    lexical_min_token_len: int = 2
    vector_score_floor: float = 0.0


@dataclass(slots=True)
class CandidateRecallResult:
    drafts: list[ClueCandidateDraft] = field(default_factory=list)
    relationship_source: RelationshipSourceResult | None = None
    hierarchy_build_id: str = ""
    hierarchy_checksum: str = ""
    source_snapshot_hash: str = ""


class ClueCandidateRecallService:
    """Build deterministic cross-chapter candidates and evidence packages."""

    def __init__(
        self,
        *,
        relationship_source: VersionedRelationshipObservationSource | None = None,
    ) -> None:
        self._relationship_source = (
            relationship_source or NullRelationshipObservationSource()
        )

    async def build_candidates_from_nodes(
        self,
        *,
        owner_id: int,
        novel_id: int,
        nodes: list[HierarchyEvidenceNode],
        source_snapshot_hash: str,
        hierarchy_build_id: str,
        hierarchy_checksum: str,
        timeline_events: list[TimelineEventRef] | None = None,
        timeline_version_id: int | None = None,
        timeline_checksum: str | None = None,
        vector_scores: dict[str, float] | None = None,
        analysis_version_id: int | None = None,
        through_chapter: int | None = None,
        config: CandidateRecallConfig | None = None,
    ) -> CandidateRecallResult:
        """Pure-ish path: nodes + optional sources → stable candidate drafts."""

        cfg = config or CandidateRecallConfig()
        rel_result = await self._relationship_source.list_observations(
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=analysis_version_id,
            through_chapter=through_chapter,
        )

        drafts = self.build_drafts_from_nodes(
            owner_id=owner_id,
            novel_id=novel_id,
            nodes=nodes,
            source_snapshot_hash=source_snapshot_hash,
            hierarchy_build_id=hierarchy_build_id,
            hierarchy_checksum=hierarchy_checksum,
            timeline_events=timeline_events or [],
            timeline_version_id=timeline_version_id,
            timeline_checksum=timeline_checksum,
            vector_scores=vector_scores or {},
            relationship_result=rel_result,
            config=cfg,
        )
        return CandidateRecallResult(
            drafts=drafts,
            relationship_source=rel_result,
            hierarchy_build_id=hierarchy_build_id,
            hierarchy_checksum=hierarchy_checksum,
            source_snapshot_hash=source_snapshot_hash,
        )

    def build_drafts_from_nodes(
        self,
        *,
        owner_id: int,
        novel_id: int,
        nodes: list[HierarchyEvidenceNode],
        source_snapshot_hash: str,
        hierarchy_build_id: str,
        hierarchy_checksum: str,
        timeline_events: list[TimelineEventRef] | None = None,
        timeline_version_id: int | None = None,
        timeline_checksum: str | None = None,
        vector_scores: dict[str, float] | None = None,
        relationship_result: RelationshipSourceResult | None = None,
        config: CandidateRecallConfig | None = None,
    ) -> list[ClueCandidateDraft]:
        """Deterministic draft construction (no I/O)."""

        cfg = config or CandidateRecallConfig()
        # Prefer evidence-level; fall back to all nodes with valid offsets.
        preferred = [n for n in nodes if n.level == "evidence"]
        working = preferred or [
            n for n in nodes if n.source_end > n.source_start and n.content
        ]
        if not working:
            return []

        ordered = sorted(
            working,
            key=lambda n: (
                n.narrative_chapter_number,
                n.source_start,
                n.order_index,
                n.node_id,
            ),
        )
        scores = dict(vector_scores or {})
        timeline_events = list(timeline_events or [])
        rel_signals = (
            relationship_result.recall_signals()
            if relationship_result is not None
            else {}
        )

        pairs = self._enumerate_cross_chapter_pairs(ordered, cfg)
        drafts: list[ClueCandidateDraft] = []
        for cue_node, later_nodes in pairs:
            if len(drafts) >= cfg.max_candidates:
                break
            draft = self._draft_from_pair(
                owner_id=owner_id,
                novel_id=novel_id,
                cue_node=cue_node,
                later_nodes=later_nodes,
                source_snapshot_hash=source_snapshot_hash,
                hierarchy_build_id=hierarchy_build_id,
                hierarchy_checksum=hierarchy_checksum,
                timeline_events=timeline_events,
                timeline_version_id=timeline_version_id,
                timeline_checksum=timeline_checksum,
                vector_scores=scores,
                relationship_signals=rel_signals,
                config=cfg,
            )
            if draft is not None:
                drafts.append(draft)

        # Stable order: candidate_id then package_hash.
        drafts.sort(key=lambda d: (d.candidate_id, d.package_hash))
        return drafts[: cfg.max_candidates]

    def _enumerate_cross_chapter_pairs(
        self,
        ordered: list[HierarchyEvidenceNode],
        cfg: CandidateRecallConfig,
    ) -> list[tuple[HierarchyEvidenceNode, list[HierarchyEvidenceNode]]]:
        pairs: list[tuple[HierarchyEvidenceNode, list[HierarchyEvidenceNode]]] = []
        for i, cue in enumerate(ordered):
            later: list[HierarchyEvidenceNode] = []
            for later_node in ordered[i + 1 :]:
                gap = later_node.narrative_chapter_number - cue.narrative_chapter_number
                if gap < cfg.min_chapter_gap:
                    # Same chapter or earlier narrative position: not later window.
                    if (
                        later_node.narrative_chapter_number
                        < cue.narrative_chapter_number
                    ):
                        continue
                    if (
                        later_node.narrative_chapter_number
                        == cue.narrative_chapter_number
                    ):
                        if later_node.source_start <= cue.source_start:
                            continue
                        # Same-chapter later offsets allowed only when min_gap is 0.
                        if cfg.min_chapter_gap > 0:
                            continue
                if gap > cfg.adjacency_chapter_window:
                    continue
                later.append(later_node)
            if later:
                pairs.append((cue, later))
        return pairs

    def _draft_from_pair(
        self,
        *,
        owner_id: int,
        novel_id: int,
        cue_node: HierarchyEvidenceNode,
        later_nodes: list[HierarchyEvidenceNode],
        source_snapshot_hash: str,
        hierarchy_build_id: str,
        hierarchy_checksum: str,
        timeline_events: list[TimelineEventRef],
        timeline_version_id: int | None,
        timeline_checksum: str | None,
        vector_scores: dict[str, float],
        relationship_signals: dict[str, Any],
        config: CandidateRecallConfig,
    ) -> ClueCandidateDraft | None:
        cue_unit = self._node_to_unit(cue_node, role_hint="cue")
        later_units = [self._node_to_unit(n, role_hint="later") for n in later_nodes]

        # Score later units for trimming.
        later_scores: dict[str, float] = {}
        reason_codes: list[str] = []
        recall_signals: dict[str, Any] = {}

        cue_tokens = _tokens(cue_node.content, config.lexical_min_token_len)
        cue_entities = set(cue_node.entities) | _infer_entities(cue_node.content)

        lexical_hits: list[dict[str, Any]] = []
        entity_hits: list[dict[str, Any]] = []
        adjacency_hits: list[dict[str, Any]] = []

        for unit, node in zip(later_units, later_nodes, strict=True):
            score = 0.0
            later_tokens = _tokens(node.content, config.lexical_min_token_len)
            shared_lex = sorted(cue_tokens & later_tokens)
            if shared_lex:
                score += min(1.0, 0.15 * len(shared_lex))
                lexical_hits.append(
                    {
                        "evidence_id": unit.evidence_id,
                        "shared_tokens": shared_lex[:8],
                    }
                )
                reason_codes.append("lexical_overlap")

            later_entities = set(node.entities) | _infer_entities(node.content)
            shared_ent = sorted(cue_entities & later_entities)
            if shared_ent:
                score += min(1.0, 0.25 * len(shared_ent))
                entity_hits.append(
                    {
                        "evidence_id": unit.evidence_id,
                        "shared_entities": shared_ent[:8],
                    }
                )
                reason_codes.append("entity_overlap")

            chapter_gap = (
                node.narrative_chapter_number - cue_node.narrative_chapter_number
            )
            if 0 <= chapter_gap <= config.adjacency_chapter_window:
                score += max(0.05, 0.2 - 0.02 * chapter_gap)
                adjacency_hits.append(
                    {
                        "evidence_id": unit.evidence_id,
                        "chapter_gap": chapter_gap,
                    }
                )
                reason_codes.append("adjacency")

            vscore = float(vector_scores.get(node.node_id, 0.0))
            if vscore >= config.vector_score_floor and vscore > 0:
                score += vscore
                reason_codes.append("vector")
            later_scores[unit.evidence_id] = score

        if lexical_hits:
            recall_signals["lexical"] = lexical_hits
        if entity_hits:
            recall_signals["entity_overlap"] = entity_hits
        if adjacency_hits:
            recall_signals["adjacency"] = adjacency_hits
        vector_hits = {
            n.node_id: float(vector_scores[n.node_id])
            for n in later_nodes
            if n.node_id in vector_scores and float(vector_scores[n.node_id]) > 0
        }
        if vector_hits:
            recall_signals["vector"] = vector_hits

        # Timeline optional signal.
        timeline_hits = []
        for ev in timeline_events:
            if ev.narrative_chapter_number >= cue_node.narrative_chapter_number and any(
                u.chapter_id == ev.chapter_id
                or abs(u.narrative_chapter_number - ev.narrative_chapter_number) <= 1
                for u in later_units
            ):
                timeline_hits.append(
                    {
                        "event_id": ev.event_id,
                        "chapter_id": ev.chapter_id,
                        "title": ev.title,
                    }
                )
                reason_codes.append("timeline_event")
        if timeline_hits:
            recall_signals["timeline"] = timeline_hits

        if relationship_signals:
            recall_signals.update(relationship_signals)
            rel = relationship_signals.get("relationship") or {}
            if rel.get("status") == "source_unavailable":
                reason_codes.append("relationship_source_unavailable")
            elif int(rel.get("count") or 0) > 0:
                reason_codes.append("relationship_observation")

        # Keep candidates even with weak signals so packages are reproducible;
        # gates/LLM still decide acceptance. Require at least adjacency or more.
        unique_reasons = sorted(set(reason_codes))
        if not unique_reasons:
            return None

        # Clamp later window to unit + chapter-span bounds before package build.
        # Recall adjacency may scan farther than MAX_LATER_CHAPTERS; keep units
        # closest to the cue (densest chapters as tie-break) instead of failing.
        trimmed_later, omitted = clamp_later_units_to_scope(
            later_units,
            max_units=config.max_later_units,
            max_chapters=config.max_later_chapters,
            scores=later_scores,
            cue_chapter=cue_node.narrative_chapter_number,
        )
        if not trimmed_later:
            return None

        cue_list, cue_omitted = trim_units_deterministically(
            [cue_unit],
            limit=config.max_cue_units,
            scores={cue_unit.evidence_id: 1.0},
        )
        omitted_all = omitted + cue_omitted

        candidate_id = stable_candidate_id(
            cue_id=cue_unit.evidence_id,
            later_ids=[u.evidence_id for u in trimmed_later],
            reason_codes=unique_reasons,
        )
        try:
            package = build_clue_evidence_package(
                owner_id=owner_id,
                novel_id=novel_id,
                candidate_id=candidate_id,
                source_snapshot_hash=source_snapshot_hash,
                hierarchy_build_id=hierarchy_build_id,
                hierarchy_checksum=hierarchy_checksum,
                cue_units=cue_list,
                later_units=trimmed_later,
                timeline_version_id=timeline_version_id,
                timeline_checksum=timeline_checksum,
                recall_signals=recall_signals,
                omitted_evidence_ids=omitted_all,
            )
        except ClueEvidenceScopeError:
            # Skip this pair; do not fail the whole novel run.
            logger.debug(
                "skipping clue candidate due to evidence scope: cue=%s later_count=%s",
                cue_unit.evidence_id,
                len(trimmed_later),
                exc_info=True,
            )
            return None
        return ClueCandidateDraft(
            candidate_id=candidate_id,
            owner_id=owner_id,
            novel_id=novel_id,
            package=package,
            recall_signals=dict(recall_signals),
            reason_codes=unique_reasons,
        )

    def _node_to_unit(
        self,
        node: HierarchyEvidenceNode,
        *,
        role_hint: str,
    ) -> ClueEvidenceUnit:
        return make_clue_evidence_unit(
            evidence_id=f"ev-{node.node_id}",
            chapter_id=node.chapter_id,
            narrative_chapter_number=node.narrative_chapter_number,
            text=node.content,
            source_start=node.source_start,
            source_end=node.source_end,
            role_hint=role_hint,  # type: ignore[arg-type]
            hierarchy_node_id=node.node_id,
            content_hash=node.content_hash if len(node.content_hash) == 64 else None,
        )

    async def load_hierarchy_nodes_from_pg(
        self,
        session: Any,
        *,
        novel_id: int,
        build_id: str | None = None,
    ) -> tuple[list[HierarchyEvidenceNode], str, str, str]:
        """Load active Phase 07 hierarchy evidence nodes (optional DB path)."""

        from app.services.chunking import pg_store

        active_build = build_id or await pg_store.get_active_build_id(session, novel_id)
        if not active_build:
            return [], "", "", ""
        build = await pg_store.get_build(session, active_build)
        if build is None:
            return [], "", "", ""
        trees = await pg_store.load_hierarchy_trees(session, active_build)
        nodes: list[HierarchyEvidenceNode] = []
        for tree in trees:
            for n in tree.nodes:
                if n.level not in {"evidence", "scene"}:
                    continue
                nodes.append(
                    HierarchyEvidenceNode(
                        node_id=n.node_id,
                        chapter_id=n.chapter_id,
                        narrative_chapter_number=n.chapter_number,
                        source_start=n.source_start,
                        source_end=n.source_end,
                        content_hash=n.content_hash,
                        content=n.content or "",
                        level=str(n.level),
                        order_index=n.order_index,
                    )
                )
        snapshot = build.source_snapshot_hash or ("0" * 64)
        checksum = build.manifest_checksum or ("0" * 64)
        return nodes, active_build, checksum, snapshot


def stable_candidate_id(
    *,
    cue_id: str,
    later_ids: list[str],
    reason_codes: list[str],
) -> str:
    """Stable candidate identity from cue/later membership and reason codes."""

    digest = sha256_json(
        {
            "cue": cue_id,
            "later": sorted(later_ids),
            "reasons": sorted(set(reason_codes)),
        }
    )
    return f"{CANDIDATE_ID_PREFIX}-{digest[:16]}"


def _tokens(text: str, min_len: int) -> set[str]:
    return {
        t
        for t in re.findall(r"[\w\u4e00-\u9fff]+", (text or "").lower())
        if len(t) >= min_len
    }


_ENTITY_PATTERN = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b|[\u4e00-\u9fff]{2,4}"
)


def _infer_entities(text: str) -> set[str]:
    found: set[str] = set()
    for match in _ENTITY_PATTERN.findall(text or ""):
        # findall with groups can return tuples
        if isinstance(match, tuple):
            token = next((m for m in match if m), "")
        else:
            token = match
        token = (token or "").strip()
        if token and token.lower() not in {"the", "and", "but", "when", "then"}:
            found.add(token)
    return found


clue_candidate_recall_service = ClueCandidateRecallService()
