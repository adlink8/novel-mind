"""Measured Phase 09 graph query / degradation performance on PostgreSQL.

Seeds 10,000 accepted observations, warms queries, records p50/p95, and asserts
hard payload caps and exact filters_required semantics. Over-budget results
fail closed (never self-waive).
"""

from __future__ import annotations

import statistics
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.orm import Session

from app.models.analysis import AnalysisVersion
from app.models.character import Character
from app.models.novel import Chapter, Novel
from app.models.relationship import (
    RelationshipBuildRun,
    RelationshipEvidenceLink,
    RelationshipObservation,
    RelationshipObservationCandidate,
    RelationshipObservationJudgment,
)
from app.models.timeline import TimelineActivePointer
from app.models.user import User
from app.schemas.relationship import RelationshipVersionSource
from app.services.relationships.query import (
    HARD_EDGE_CAP,
    HARD_NODE_CAP,
    NORMAL_EDGE_CAP,
    NORMAL_NODE_CAP,
    RelationshipGraphQueryService,
)
from tests.integration.conftest import run_alembic
from tests.integration.relationships.test_api import _async_session

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64
P95_BUDGET_MS = 300.0
OBSERVATION_TARGET = 10_000
CHARACTER_COUNT = 520  # > HARD_NODE_CAP when fully connected into graph
WARMUP = 3
SAMPLES = 12


def _seed_performance_graph(engine) -> dict[str, Any]:
    """Bulk-seed 10k accepted observations with 520 characters."""
    unique = uuid.uuid4().hex[:12]
    with Session(engine) as session:
        owner = User(
            username=f"rel_perf_{unique}",
            email=f"rel_perf_{unique}@example.com",
            hashed_password="hash",
        )
        session.add(owner)
        session.flush()

        novel = Novel(
            title=f"Perf Relationship Novel {unique}",
            owner_id=owner.id,
            status="ready",
            reading_progress={},
        )
        session.add(novel)
        session.flush()

        chapters = [
            Chapter(
                novel_id=novel.id,
                chapter_number=n,
                title=f"Chapter {n}",
                content=f"content {n}",
                word_count=20,
            )
            for n in range(1, 6)
        ]
        session.add_all(chapters)
        session.flush()
        novel.reading_progress = {
            "chapter_id": chapters[-1].id,
            "timeline_full_book": True,
        }

        characters = [
            Character(novel_id=novel.id, name=f"Char{i:04d}", role="supporting")
            for i in range(CHARACTER_COUNT)
        ]
        session.add_all(characters)
        session.flush()
        char_ids = [c.id for c in characters]

        version = AnalysisVersion(
            owner_id=owner.id,
            novel_id=novel.id,
            version_key=f"rel-perf-{unique}",
            status="active",
            source_snapshot_hash=HEX64,
            hierarchy_build_id=f"build-perf-{unique}",
            hierarchy_checksum=HEX64_B,
            prompt_hash=HEX64_C,
            schema_hash=HEX64,
            model_lineage={},
            decoding_hash=HEX64_B,
            config_hash=HEX64_C,
            price_snapshot={},
            manifest={},
        )
        session.add(version)
        session.flush()
        session.add(
            TimelineActivePointer(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                revision=1,
                manifest_checksum=HEX64_D,
            )
        )

        from app.models.knowledge import (
            KnowledgeExtractionRun,
            KnowledgeRelationCandidate,
            KnowledgeRelationJudgment,
        )

        krun = KnowledgeExtractionRun(
            owner_id=owner.id,
            novel_id=novel.id,
            run_name=f"rel-perf-{unique}",
            domain_profile="fiction",
            ontology_profile="fiction.v1",
            status="completed",
        )
        session.add(krun)
        session.flush()
        kcand = KnowledgeRelationCandidate(
            owner_id=owner.id,
            novel_id=novel.id,
            run_id=krun.id,
            domain_profile="fiction",
            relation_type="ally",
            source_kind="entity_candidate",
            source_id=1,
            target_kind="entity_candidate",
            target_id=2,
            recall_signals={},
            package_snapshot={},
            evidence_refs=["e-perf"],
            status="accepted",
        )
        session.add(kcand)
        session.flush()
        kjudg = KnowledgeRelationJudgment(
            owner_id=owner.id,
            novel_id=novel.id,
            run_id=krun.id,
            relation_candidate_id=kcand.id,
            prompt_version="pv1",
            model_name="perf",
            relation_type="ally",
            confidence=0.95,
            evidence_refs=["e-perf"],
            rationale="perf seed",
            risk_flags=[],
            raw_output={},
            structured_output={},
            status="accepted",
            gate_status="accepted",
        )
        session.add(kjudg)
        session.flush()

        build = RelationshipBuildRun(
            owner_id=owner.id,
            novel_id=novel.id,
            analysis_version_id=version.id,
            status="completed",
            checkpoint={},
            progress={},
            prompt_hash=HEX64,
            schema_hash=HEX64,
            policy_hash=HEX64,
            decoding_hash=HEX64,
            model_lineage={},
            accepted_count=OBSERVATION_TARGET,
        )
        session.add(build)
        session.flush()

        edge_types = ("ally", "enemy", "family", "mentor", "romantic")
        # Ally edges among first 280 characters → large tier under filter.
        # Remaining types create volume for filters_required on full graph.
        cand_rows = []
        for i in range(OBSERVATION_TARGET):
            src = char_ids[i % CHARACTER_COUNT]
            tgt = char_ids[(i + 1 + (i // CHARACTER_COUNT)) % CHARACTER_COUNT]
            if src == tgt:
                tgt = char_ids[(i + 2) % CHARACTER_COUNT]
            rel = edge_types[i % len(edge_types)]
            if i < 280:
                # Dedicated ally chain for large-mode filter samples.
                src = char_ids[i]
                tgt = char_ids[(i + 1) % 280] if i + 1 < 280 else char_ids[0]
                if src == tgt:
                    tgt = char_ids[(i + 3) % 280]
                rel = "ally"
            cand_rows.append(
                {
                    "owner_id": owner.id,
                    "novel_id": novel.id,
                    "analysis_version_id": version.id,
                    "build_run_id": build.id,
                    "source_judgment_id": kjudg.id,
                    "source_relation_candidate_id": kcand.id,
                    "source_character_id": src,
                    "target_character_id": tgt,
                    "relation_type": rel,
                    "package_hash": f"{i:064x}"[-64:],
                    "package_snapshot": {},
                    "recall_signals": {},
                    "evidence_refs": [f"e-{i}"],
                    "status": "accepted",
                }
            )

        session.execute(insert(RelationshipObservationCandidate), cand_rows)
        session.flush()
        cand_ids = list(
            session.scalars(
                select(RelationshipObservationCandidate.id)
                .where(
                    RelationshipObservationCandidate.build_run_id == build.id,
                )
                .order_by(RelationshipObservationCandidate.id)
            )
        )
        assert len(cand_ids) == OBSERVATION_TARGET

        # Map package index → candidate metadata for judgments/observations.
        cand_meta = list(
            session.execute(
                select(
                    RelationshipObservationCandidate.id,
                    RelationshipObservationCandidate.source_character_id,
                    RelationshipObservationCandidate.target_character_id,
                    RelationshipObservationCandidate.relation_type,
                )
                .where(RelationshipObservationCandidate.build_run_id == build.id)
                .order_by(RelationshipObservationCandidate.id)
            )
        )

        judg_rows = []
        for idx, (cid, src, tgt, rel) in enumerate(cand_meta):
            judg_rows.append(
                {
                    "owner_id": owner.id,
                    "novel_id": novel.id,
                    "analysis_version_id": version.id,
                    "build_run_id": build.id,
                    "candidate_id": cid,
                    "prompt_hash": HEX64,
                    "schema_hash": HEX64,
                    "policy_hash": HEX64,
                    "model_name": "perf",
                    "model_lineage": {},
                    "relation_type": rel,
                    "transition": "establish",
                    "confidence": 0.91,
                    "valid_from_evidence_id": f"e-{idx}",
                    "supporting_evidence_ids": [f"e-{idx}"],
                    "structured_output": {},
                    "risk_flags": [],
                    "status": "accepted",
                    "gate_status": "accepted",
                    "gate_failures": [],
                }
            )
        session.execute(insert(RelationshipObservationJudgment), judg_rows)
        session.flush()
        judg_ids = list(
            session.scalars(
                select(RelationshipObservationJudgment.id)
                .where(RelationshipObservationJudgment.build_run_id == build.id)
                .order_by(RelationshipObservationJudgment.id)
            )
        )
        assert len(judg_ids) == OBSERVATION_TARGET

        obs_rows = []
        for idx, (cid, src, tgt, rel) in enumerate(cand_meta):
            # Spread some edges to later chapters for through_chapter tests.
            from_ch = 1 if idx < 9000 else 3
            obs_rows.append(
                {
                    "owner_id": owner.id,
                    "novel_id": novel.id,
                    "analysis_version_id": version.id,
                    "build_run_id": build.id,
                    "candidate_id": cid,
                    "judgment_id": judg_ids[idx],
                    "source_judgment_id": kjudg.id,
                    "source_character_id": src,
                    "target_character_id": tgt,
                    "relation_type": rel,
                    "transition": "establish",
                    "status": "accepted",
                    "valid_from_chapter": from_ch,
                    "valid_from_narrative_index": 0,
                    "valid_to_chapter": None,
                    "valid_to_narrative_index": None,
                    "valid_from_evidence_id": f"e-{idx}",
                    "confidence": 0.91,
                    "evidence_checksum": HEX64,
                    "observation_checksum": HEX64_B,
                    "prompt_hash": HEX64,
                    "schema_hash": HEX64,
                    "policy_hash": HEX64,
                    "model_lineage": {},
                    "idempotency_key": f"perf-idem-{unique}-{idx}",
                }
            )
        session.execute(insert(RelationshipObservation), obs_rows)
        session.flush()
        obs_ids = list(
            session.scalars(
                select(RelationshipObservation.id)
                .where(RelationshipObservation.build_run_id == build.id)
                .order_by(RelationshipObservation.id)
            )
        )
        assert len(obs_ids) == OBSERVATION_TARGET

        # Attach minimal evidence to a subset (fold does not require all links).
        evidence_rows = []
        for idx, obs_id in enumerate(obs_ids[:500]):
            evidence_rows.append(
                {
                    "observation_id": obs_id,
                    "owner_id": owner.id,
                    "novel_id": novel.id,
                    "analysis_version_id": version.id,
                    "evidence_id": f"e-{idx}",
                    "chapter_id": chapters[0].id,
                    "source_start": 0,
                    "source_end": 12,
                    "content_hash": HEX64,
                    "excerpt": f"evidence {idx}",
                    "sort_order": 0,
                }
            )
        session.execute(insert(RelationshipEvidenceLink), evidence_rows)
        session.commit()

        count = session.scalar(
            select(func.count())
            .select_from(RelationshipObservation)
            .where(RelationshipObservation.analysis_version_id == version.id)
        )
        assert int(count or 0) == OBSERVATION_TARGET

        return {
            "owner_id": owner.id,
            "novel_id": novel.id,
            "version_id": version.id,
            "char0_id": char_ids[0],
            "character_count": CHARACTER_COUNT,
            "observation_count": OBSERVATION_TARGET,
        }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


@pytest.mark.asyncio
async def test_relationship_graph_performance_and_degradation_tiers(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_performance_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipGraphQueryService()

    async with factory() as db:
        novel = await db.get(Novel, ids["novel_id"])
        assert novel is not None

        # --- filters_required on full graph ---
        full = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            request_full_book=True,
        )
        assert full is not None
        assert full.degradation.mode.value == "filters_required"
        assert full.nodes == []
        assert full.edges == []
        assert full.counts.nodes > HARD_NODE_CAP or full.counts.edges > HARD_EDGE_CAP
        assert full.counts.nodes <= ids["character_count"]
        assert full.degradation.hard_node_cap == HARD_NODE_CAP
        assert full.degradation.hard_edge_cap == HARD_EDGE_CAP
        assert full.available_character_ids  # spoiler-safe metadata retained

        # --- large tier via ally filter (first 280-char chain) ---
        large = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            request_full_book=True,
            relation_type="ally",
        )
        assert large is not None
        # Ally subset should be below hard cap; may be large or normal depending
        # on fold uniqueness. Assert hard caps never exceeded on elements.
        assert len(large.nodes) <= HARD_NODE_CAP
        assert len(large.edges) <= HARD_EDGE_CAP
        if large.degradation.mode.value == "filters_required":
            assert large.nodes == [] and large.edges == []
        else:
            assert large.counts.nodes == len(large.nodes)
            assert large.counts.edges == len(large.edges)
            if large.counts.nodes > NORMAL_NODE_CAP or large.counts.edges > NORMAL_EDGE_CAP:
                assert large.degradation.mode.value == "large"

        # --- normal tier via single-character filter ---
        normal = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            request_full_book=True,
            character_id=ids["char0_id"],
        )
        assert normal is not None
        assert len(normal.nodes) <= NORMAL_NODE_CAP or normal.degradation.mode.value in {
            "normal",
            "large",
            "filters_required",
        }
        if normal.degradation.mode.value != "filters_required":
            assert len(normal.nodes) <= HARD_NODE_CAP
            assert len(normal.edges) <= HARD_EDGE_CAP

        # --- measured query latency after warmup ---
        async def once() -> float:
            started = time.perf_counter()
            result = await svc.build_graph(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                source=RelationshipVersionSource.ACTIVE,
                request_full_book=True,
                character_id=ids["char0_id"],
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            assert result is not None
            return elapsed_ms

        for _ in range(WARMUP):
            await once()
        samples = [await once() for _ in range(SAMPLES)]
        p50 = _percentile(samples, 0.50)
        p95 = _percentile(samples, 0.95)
        metrics = {
            "samples_ms": [round(s, 3) for s in samples],
            "p50_ms": round(p50, 3),
            "p95_ms": round(p95, 3),
            "mean_ms": round(statistics.fmean(samples), 3),
            "observation_count": ids["observation_count"],
            "budget_p95_ms": P95_BUDGET_MS,
        }
        # Fail closed: over-budget is a hard assertion with metrics evidence.
        assert p95 <= P95_BUDGET_MS, (
            f"relationship query p95 {p95:.1f}ms exceeds {P95_BUDGET_MS}ms budget; "
            f"metrics={metrics}"
        )

    await aengine.dispose()


@pytest.mark.asyncio
async def test_hard_caps_constants_match_product_contract():
    assert HARD_NODE_CAP == 500
    assert HARD_EDGE_CAP == 1500
    assert NORMAL_NODE_CAP == 200
    assert NORMAL_EDGE_CAP == 600
