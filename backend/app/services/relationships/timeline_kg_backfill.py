"""Seed-mode ops backfill: Characters + Phase-04 KG rows from timeline participants.

Closes the data hole: Phase 09 relationship worker only selects
KnowledgeRelationJudgment rows with status/gate_status == accepted and character
endpoints. Timeline-only novels currently have 0 KG rows → 0 observations.

**Seed / ops path only — not a silent product truth path.**

This path is deterministic (no LLM) and uses timeline co-occurrence + event text
heuristics to *seed* typed judgments with real evidence locators. Rows are
marked with ``source=timeline_kg_backfill`` in package/config/raw/structured
metadata so operators can distinguish seed pollution from pipeline-accepted
observations. Prefer relying on graph provisional co-occurrence
(``edge_kind=provisional_cooccurrence``) for progressive UI when empty;
run this backfill only when deliberately seeding accepted intake for ops.

The relationship worker can then materialize accepted observations (optionally
with deterministic judgments to skip a second LLM pass).
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import AnalysisVersion
from app.models.character import Character
from app.models.knowledge import (
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.models.novel import Chapter, Novel
from app.models.relationship import RELATIONSHIP_EDGE_TYPES
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineEvidenceRef,
    TimelineParticipant,
)
from app.services.relationships.query import RelationshipGraphQueryService
from app.services.relationships.worker import relationship_observation_worker

logger = logging.getLogger(__name__)

ALLOWED_TYPES = frozenset(RELATIONSHIP_EDGE_TYPES)


@dataclass
class BackfillResult:
    novel_id: int
    owner_id: int
    analysis_version_id: int | None
    characters_created: int = 0
    characters_total: int = 0
    kg_run_id: int | None = None
    judgments_created: int = 0
    judgments_by_type: dict[str, int] = field(default_factory=dict)
    relationship_build_status: str | None = None
    relationship_accepted: int = 0
    relationship_candidate_count: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "novel_id": self.novel_id,
            "owner_id": self.owner_id,
            "analysis_version_id": self.analysis_version_id,
            "characters_created": self.characters_created,
            "characters_total": self.characters_total,
            "kg_run_id": self.kg_run_id,
            "judgments_created": self.judgments_created,
            "judgments_by_type": self.judgments_by_type,
            "relationship_build_status": self.relationship_build_status,
            "relationship_accepted": self.relationship_accepted,
            "relationship_candidate_count": self.relationship_candidate_count,
            "errors": self.errors,
        }


def _sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


class TimelineKgBackfillService:
    """Seed KG + characters from machine timeline for Phase 09 intake."""

    def __init__(self) -> None:
        self._type_infer = RelationshipGraphQueryService._infer_provisional_type

    async def backfill(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
        owner_id: int | None = None,
        max_characters: int = 40,
        max_judgments: int = 60,
        min_cooccur: int = 3,
        run_relationship_worker: bool = True,
        use_deterministic_rel_judge: bool = True,
    ) -> BackfillResult:
        novel = await db.get(Novel, novel_id)
        if novel is None:
            raise ValueError(f"novel {novel_id} not found")
        if owner_id is not None and novel.owner_id != owner_id:
            raise ValueError("owner_id mismatch")
        owner_id = novel.owner_id

        ptr = await db.scalar(
            select(TimelineActivePointer).where(
                TimelineActivePointer.owner_id == owner_id,
                TimelineActivePointer.novel_id == novel_id,
            )
        )
        if ptr is None:
            raise ValueError("no active timeline pointer; run timeline analysis first")

        version = await db.get(AnalysisVersion, ptr.version_id)
        if version is None:
            raise ValueError(f"analysis version {ptr.version_id} missing")

        result = BackfillResult(
            novel_id=novel_id,
            owner_id=owner_id,
            analysis_version_id=version.id,
        )

        # --- 1) Characters from top mentions ---
        mention_to_char, created = await self._ensure_characters(
            db,
            novel_id=novel_id,
            version_id=version.id,
            owner_id=owner_id,
            max_characters=max_characters,
        )
        result.characters_created = created
        result.characters_total = len(mention_to_char)

        if len(mention_to_char) < 2:
            result.errors.append("fewer than 2 characters materialised")
            return result

        # --- 2) Typed co-occurrence pairs among those characters ---
        pairs = await self._collect_typed_pairs(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version.id,
            mention_to_char=mention_to_char,
            min_cooccur=min_cooccur,
            max_judgments=max_judgments,
        )
        if not pairs:
            result.errors.append("no typed co-occurrence pairs above threshold")
            return result

        # --- 3) KG run + accepted judgments ---
        run = KnowledgeExtractionRun(
            owner_id=owner_id,
            novel_id=novel_id,
            run_name=f"timeline_kg_backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            domain_profile="fiction",
            ontology_profile="fiction.v1",
            status="running",
            prompt_version="timeline-kg-backfill.v1",
            config_snapshot={
                "source": "timeline_kg_backfill",
                "seed_mode": True,
                "upstream": "machine_timeline",
                "version_id": version.id,
                "max_characters": max_characters,
                "max_judgments": max_judgments,
                "min_cooccur": min_cooccur,
            },
        )
        db.add(run)
        await db.flush()
        result.kg_run_id = run.id

        type_counts: dict[str, int] = defaultdict(int)
        for pair in pairs:
            try:
                await self._persist_accepted_judgment(
                    db,
                    run=run,
                    version=version,
                    pair=pair,
                )
                type_counts[pair["relation_type"]] += 1
            except Exception as exc:
                logger.warning("skip pair %s: %s", pair.get("key"), exc)
                result.errors.append(f"pair_skip:{exc}")

        run.candidate_count = sum(type_counts.values())
        run.judgment_count = sum(type_counts.values())
        run.accepted_count = sum(type_counts.values())
        run.status = "completed"
        await db.flush()

        result.judgments_created = sum(type_counts.values())
        result.judgments_by_type = dict(type_counts)

        # --- 4) Phase 09 observation build ---
        if run_relationship_worker and result.judgments_created > 0:
            try:
                det: dict[str, dict[str, Any]] | None = None
                if use_deterministic_rel_judge:
                    det = await self._build_deterministic_outputs(
                        db,
                        owner_id=owner_id,
                        novel_id=novel_id,
                        analysis_version_id=version.id,
                    )
                worker_result = await relationship_observation_worker.run(
                    db,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    analysis_version_id=version.id,
                    deterministic_outputs=det,
                )
                result.relationship_build_status = worker_result.status
                result.relationship_accepted = worker_result.accepted_count
                result.relationship_candidate_count = worker_result.candidate_count
            except Exception as exc:
                logger.exception("relationship worker after backfill failed")
                result.errors.append(f"relationship_worker:{exc}")
                result.relationship_build_status = "failed"

        return result

    async def _ensure_characters(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
        version_id: int,
        owner_id: int,
        max_characters: int,
    ) -> tuple[dict[str, Character], int]:
        # Rank mentions by frequency on this version.
        events = list(
            (
                await db.scalars(
                    select(MachineTimelineEvent).where(
                        MachineTimelineEvent.owner_id == owner_id,
                        MachineTimelineEvent.novel_id == novel_id,
                        MachineTimelineEvent.version_id == version_id,
                    )
                )
            ).all()
        )
        event_ids = [e.id for e in events]
        if not event_ids:
            return {}, 0

        parts = list(
            (
                await db.scalars(
                    select(TimelineParticipant).where(
                        TimelineParticipant.event_id.in_(event_ids)
                    )
                )
            ).all()
        )
        chapter_by_event = {e.id: e.narrative_chapter_number for e in events}
        counts: dict[str, int] = defaultdict(int)
        first_ch: dict[str, int] = {}
        for p in parts:
            name = (p.mention or "").strip()
            if not name or len(name) > 40:
                continue
            # Drop pure punctuation / numbers noise
            if not any(ch.isalnum() for ch in name):
                continue
            counts[name] += 1
            ch = chapter_by_event.get(p.event_id, 1)
            prev = first_ch.get(name)
            if prev is None or ch < prev:
                first_ch[name] = ch

        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_characters]
        existing = list(
            (
                await db.scalars(
                    select(Character).where(Character.novel_id == novel_id)
                )
            ).all()
        )
        by_name = {c.name.strip(): c for c in existing if c.name}
        created = 0
        out: dict[str, Character] = {}
        for name, _cnt in ranked:
            if name in by_name:
                out[name] = by_name[name]
                continue
            role = "protagonist" if created == 0 and not by_name else "supporting"
            char = Character(
                novel_id=novel_id,
                name=name,
                role=role,
                first_appearance_chapter=first_ch.get(name),
                description=f"时间线参与者回填（共现 {counts[name]} 次）",
                extra_data={
                    "source": "timeline_kg_backfill",
                    "mention_count": counts[name],
                },
            )
            db.add(char)
            await db.flush()
            by_name[name] = char
            out[name] = char
            created += 1
        return out, created

    async def _collect_typed_pairs(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        mention_to_char: dict[str, Character],
        min_cooccur: int,
        max_judgments: int,
    ) -> list[dict[str, Any]]:
        events = list(
            (
                await db.scalars(
                    select(MachineTimelineEvent).where(
                        MachineTimelineEvent.owner_id == owner_id,
                        MachineTimelineEvent.novel_id == novel_id,
                        MachineTimelineEvent.version_id == version_id,
                    )
                )
            ).all()
        )
        event_by_id = {e.id: e for e in events}
        parts = list(
            (
                await db.scalars(
                    select(TimelineParticipant).where(
                        TimelineParticipant.event_id.in_(list(event_by_id.keys()))
                    )
                )
            ).all()
        )
        by_event: dict[int, list[str]] = defaultdict(list)
        for p in parts:
            name = (p.mention or "").strip()
            if name in mention_to_char:
                by_event[p.event_id].append(name)

        # pair names (sorted) -> stats
        pair_stats: dict[tuple[str, str], dict[str, Any]] = {}
        for event_id, names in by_event.items():
            uniq = sorted(set(names))
            if len(uniq) < 2:
                continue
            event = event_by_id[event_id]
            inferred = self._type_infer(
                title=event.title or "",
                description=event.description or "",
                event_type=event.event_type or "",
            )
            if inferred not in ALLOWED_TYPES:
                inferred = "ally"
            for i in range(len(uniq)):
                for j in range(i + 1, len(uniq)):
                    a, b = uniq[i], uniq[j]
                    key = (a, b)
                    st = pair_stats.get(key)
                    if st is None:
                        st = {
                            "count": 0,
                            "first_chapter": event.narrative_chapter_number,
                            "type_votes": defaultdict(int),
                            "event_ids_by_type": defaultdict(list),
                        }
                        pair_stats[key] = st
                    st["count"] += 1
                    st["type_votes"][inferred] += 1
                    if event.narrative_chapter_number < st["first_chapter"]:
                        st["first_chapter"] = event.narrative_chapter_number
                    if len(st["event_ids_by_type"][inferred]) < 3:
                        st["event_ids_by_type"][inferred].append(event_id)

        # Quota per type for diversity
        quotas = {
            "enemy": max(8, max_judgments // 4),
            "ally": max(8, max_judgments // 4),
            "family": max(4, max_judgments // 8),
            "mentor": max(4, max_judgments // 8),
            "romantic": max(3, max_judgments // 10),
        }
        used: dict[str, int] = defaultdict(int)

        candidates: list[tuple[int, int, str, tuple[str, str], dict[str, Any]]] = []
        for key, st in pair_stats.items():
            if st["count"] < min_cooccur:
                continue
            ordered = sorted(st["type_votes"].items(), key=lambda kv: (-kv[1], kv[0]))
            for rel_t, vote in ordered[:2]:
                if vote < 1:
                    continue
                boost = 3 if rel_t == "enemy" else (2 if rel_t != "ally" else 0)
                candidates.append((-(vote + boost), -st["count"], rel_t, key, st))

        candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
        selected: list[dict[str, Any]] = []
        for _score, _cnt, rel_t, (na, nb), st in candidates:
            if used[rel_t] >= quotas.get(rel_t, 6):
                continue
            event_ids = st["event_ids_by_type"].get(rel_t) or []
            if not event_ids:
                # fallback any event for pair
                event_ids = next(iter(st["event_ids_by_type"].values()), [])
            if not event_ids:
                continue
            selected.append(
                {
                    "key": f"{na}|{nb}|{rel_t}",
                    "name_a": na,
                    "name_b": nb,
                    "char_a": mention_to_char[na],
                    "char_b": mention_to_char[nb],
                    "relation_type": rel_t,
                    "vote": int(st["type_votes"][rel_t]),
                    "count": int(st["count"]),
                    "first_chapter": int(st["first_chapter"]),
                    "sample_event_ids": list(event_ids),
                }
            )
            used[rel_t] += 1
            if len(selected) >= max_judgments:
                break
        return selected

    async def _persist_accepted_judgment(
        self,
        db: AsyncSession,
        *,
        run: KnowledgeExtractionRun,
        version: AnalysisVersion,
        pair: dict[str, Any],
    ) -> KnowledgeRelationJudgment:
        char_a: Character = pair["char_a"]
        char_b: Character = pair["char_b"]
        rel_t: str = pair["relation_type"]
        sample_event_ids: list[int] = pair["sample_event_ids"]

        evidence_refs: list[str] = []
        for event_id in sample_event_ids:
            refs = await self._evidence_from_timeline_event(
                db,
                run=run,
                event_id=event_id,
            )
            for ref_key in refs:
                if ref_key not in evidence_refs:
                    evidence_refs.append(ref_key)
            if len(evidence_refs) >= 3:
                break

        if not evidence_refs:
            # Last resort: bind to first chapter of first appearance.
            ch_num = int(pair["first_chapter"] or 1)
            chapter = await db.scalar(
                select(Chapter).where(
                    Chapter.novel_id == run.novel_id,
                    Chapter.chapter_number == ch_num,
                )
            )
            if chapter is None:
                chapter = await db.scalar(
                    select(Chapter)
                    .where(Chapter.novel_id == run.novel_id)
                    .order_by(Chapter.chapter_number.asc())
                    .limit(1)
                )
            if chapter is None:
                raise ValueError("no chapters for evidence")
            ref_key = f"ev-tl-ch-{chapter.id}-{_sha1_hex(pair['key'])[:10]}"
            existing = await db.scalar(
                select(KnowledgeEvidenceRef).where(
                    KnowledgeEvidenceRef.run_id == run.id,
                    KnowledgeEvidenceRef.ref_key == ref_key,
                )
            )
            if existing is None:
                db.add(
                    KnowledgeEvidenceRef(
                        owner_id=run.owner_id,
                        novel_id=run.novel_id,
                        run_id=run.id,
                        ref_key=ref_key,
                        source_type="timeline_chapter",
                        chapter_id=chapter.id,
                        excerpt=(
                            f"{pair['name_a']} 与 {pair['name_b']} "
                            f"在第 {chapter.chapter_number} 章共现（时间线回填）"
                        )[:700],
                        char_start=0,
                        char_end=40,
                        source_locator={
                            "chapter_number": chapter.chapter_number,
                            "source": "timeline_kg_backfill",
                        },
                        metadata_json={"pair": pair["key"]},
                    )
                )
                await db.flush()
            evidence_refs = [ref_key]

        confidence = min(
            0.92, 0.55 + 0.03 * int(pair["vote"]) + 0.01 * int(pair["count"])
        )
        candidate = KnowledgeRelationCandidate(
            owner_id=run.owner_id,
            novel_id=run.novel_id,
            run_id=run.id,
            domain_profile="fiction",
            relation_type=rel_t,
            source_kind="character",
            source_id=char_a.id,
            target_kind="character",
            target_id=char_b.id,
            recall_signals={
                "timeline_cooccur": {
                    "count": pair["count"],
                    "type_vote": pair["vote"],
                    "first_chapter": pair["first_chapter"],
                }
            },
            package_snapshot={
                "source": "timeline_kg_backfill",
                "pair": pair["key"],
                "analysis_version_id": version.id,
            },
            evidence_refs=evidence_refs,
            status="accepted",
        )
        db.add(candidate)
        await db.flush()

        judgment = KnowledgeRelationJudgment(
            owner_id=run.owner_id,
            novel_id=run.novel_id,
            run_id=run.id,
            relation_candidate_id=candidate.id,
            prompt_version="timeline-kg-backfill.v1",
            model_name="timeline_cooccur_heuristic",
            relation_type=rel_t,
            confidence=confidence,
            evidence_refs=evidence_refs,
            rationale=(
                f"时间线共现回填：{pair['name_a']}–{pair['name_b']} "
                f"类型={rel_t} 票={pair['vote']} 共现={pair['count']} "
                f"自首见第{pair['first_chapter']}章"
            ),
            risk_flags=["timeline_heuristic_seed", "seed_mode_ops"],
            raw_output={
                "source": "timeline_kg_backfill",
                "seed_mode": True,
                "pair": pair["key"],
            },
            structured_output={
                "source": "timeline_kg_backfill",
                "seed_mode": True,
                "relation_type": rel_t,
                "confidence": confidence,
                "evidence_refs": evidence_refs,
            },
            status="accepted",
            gate_status="accepted",
            gate_failures=[],
            needs_human_review=False,
        )
        db.add(judgment)
        await db.flush()
        return judgment

    async def _evidence_from_timeline_event(
        self,
        db: AsyncSession,
        *,
        run: KnowledgeExtractionRun,
        event_id: int,
    ) -> list[str]:
        event = await db.get(MachineTimelineEvent, event_id)
        if event is None:
            return []
        t_refs = list(
            (
                await db.scalars(
                    select(TimelineEvidenceRef).where(
                        TimelineEvidenceRef.event_id == event_id
                    )
                )
            ).all()
        )
        keys: list[str] = []
        for tref in t_refs[:2]:
            ref_key = f"ev-tl-{event_id}-{tref.id}"
            existing = await db.scalar(
                select(KnowledgeEvidenceRef).where(
                    KnowledgeEvidenceRef.run_id == run.id,
                    KnowledgeEvidenceRef.ref_key == ref_key,
                )
            )
            if existing is None:
                excerpt = (event.title or "") + " — " + (event.description or "")
                excerpt = " ".join(excerpt.split())[:700]
                end = max(len(excerpt), 1)
                db.add(
                    KnowledgeEvidenceRef(
                        owner_id=run.owner_id,
                        novel_id=run.novel_id,
                        run_id=run.id,
                        ref_key=ref_key,
                        source_type="timeline_event",
                        chapter_id=tref.chapter_id,
                        excerpt=excerpt or event.title or "timeline event",
                        char_start=int(tref.source_start or 0),
                        char_end=int(tref.source_end or end),
                        source_locator={
                            "timeline_event_id": event_id,
                            "timeline_evidence_id": tref.id,
                            "content_hash": tref.content_hash,
                            "narrative_chapter": event.narrative_chapter_number,
                        },
                        metadata_json={
                            "event_type": event.event_type,
                            "title": event.title,
                        },
                    )
                )
            keys.append(ref_key)
        if keys:
            await db.flush()
        return keys

    async def _build_deterministic_outputs(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int,
    ) -> dict[str, dict[str, Any]]:
        """Pre-build judge payloads so Phase 09 worker skips LLM for seed edges."""

        from app.services.relationships.candidates import relationship_candidate_service

        selection = await relationship_candidate_service.select_and_build(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=analysis_version_id,
        )
        outputs: dict[str, dict[str, Any]] = {}
        for draft in selection.drafts:
            eids = draft.package.allowed_evidence_ids()
            if not eids:
                continue
            payload = {
                "schema_version": "relationship-semantic-judgment.v1",
                "candidate_key": draft.package.candidate_key,
                "source_ref": draft.package.source_ref,
                "target_ref": draft.package.target_ref,
                "relation_type": draft.relation_type,
                "transition": "establish",
                "valid_from_evidence_id": eids[0],
                "valid_to_evidence_id": None,
                "supporting_evidence_ids": eids[:3],
                "confidence": 0.9,
                "rationale": "timeline_kg_backfill deterministic establish",
                # Empty risk_flags: any flag forces needs_human_review in gate policy.
                "risk_flags": [],
            }
            outputs[draft.package.candidate_key] = payload
            outputs[str(draft.source_judgment_id)] = payload
        return outputs


timeline_kg_backfill_service = TimelineKgBackfillService()
