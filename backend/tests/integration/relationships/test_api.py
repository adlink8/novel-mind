"""PostgreSQL integration tests for Phase 09 relationship graph API/query."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.analysis import AnalysisRun, AnalysisVersion
from app.models.character import Character, CharacterRelation
from app.models.novel import Chapter, Novel
from app.models.relationship import (
    RelationshipBuildRun,
    RelationshipEvidenceLink,
    RelationshipObservation,
    RelationshipObservationCandidate,
    RelationshipObservationJudgment,
    RelationshipOverride,
)
from app.models.timeline import TimelineActivePointer
from app.models.user import User
from app.schemas.relationship import RelationshipVersionSource
from app.services.relationships.query import (
    HARD_EDGE_CAP,
    HARD_NODE_CAP,
    RelationshipGraphQueryService,
    logical_relationship_key,
)
from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


def _seed_graph(engine, *, with_future: bool = True) -> dict[str, Any]:
    """Seed owner/novel/chapters/characters/versions/accepted observations."""

    with Session(engine) as session:
        owner = User(
            username="rel_api_owner",
            email="rel_api@example.com",
            hashed_password="hash",
        )
        other = User(
            username="rel_api_other",
            email="rel_other@example.com",
            hashed_password="hash",
        )
        session.add_all([owner, other])
        session.flush()

        novel = Novel(
            title="Relationship Graph Novel",
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
                word_count=10,
            )
            for n in (1, 2, 3)
        ]
        session.add_all(chapters)
        session.flush()

        alice = Character(novel_id=novel.id, name="Alice", role="protagonist")
        bob = Character(novel_id=novel.id, name="Bob", role="supporting")
        carol = Character(novel_id=novel.id, name="CarolFuture", role="supporting")
        session.add_all([alice, bob, carol])
        session.flush()

        # Legacy CharacterRelation must never affect graph truth.
        session.add(
            CharacterRelation(
                novel_id=novel.id,
                source_character_id=alice.id,
                target_character_id=bob.id,
                relation_type="friend",
                strength=9,
                description="LEGACY_SECRET_RELATION",
            )
        )

        v1 = AnalysisVersion(
            owner_id=owner.id,
            novel_id=novel.id,
            version_key="rel-v1",
            status="active",
            source_snapshot_hash=HEX64,
            hierarchy_build_id="build-v1",
            hierarchy_checksum=HEX64_B,
            prompt_hash=HEX64_C,
            schema_hash=HEX64,
            model_lineage={},
            decoding_hash=HEX64_B,
            config_hash=HEX64_C,
            price_snapshot={},
            manifest={},
        )
        v2 = AnalysisVersion(
            owner_id=owner.id,
            novel_id=novel.id,
            version_key="rel-v2-candidate",
            status="candidate",
            source_snapshot_hash=HEX64_B,
            hierarchy_build_id="build-v2",
            hierarchy_checksum=HEX64_C,
            prompt_hash=HEX64,
            schema_hash=HEX64_B,
            model_lineage={},
            decoding_hash=HEX64_C,
            config_hash=HEX64,
            price_snapshot={},
            manifest={},
        )
        session.add_all([v1, v2])
        session.flush()

        session.add(
            TimelineActivePointer(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=v1.id,
                revision=1,
                manifest_checksum=HEX64_D,
            )
        )

        # Knowledge lineage stubs required by FKs.
        from app.models.knowledge import (
            KnowledgeExtractionRun,
            KnowledgeRelationCandidate,
            KnowledgeRelationJudgment,
        )

        krun = KnowledgeExtractionRun(
            owner_id=owner.id,
            novel_id=novel.id,
            run_name="rel-api",
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
            evidence_refs=["e1"],
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
            model_name="test",
            relation_type="ally",
            confidence=0.95,
            evidence_refs=["e1"],
            rationale="ok",
            risk_flags=[],
            raw_output={},
            structured_output={},
            status="accepted",
            gate_status="accepted",
        )
        session.add(kjudg)
        session.flush()

        def _add_obs(
            *,
            version: AnalysisVersion,
            src: Character,
            tgt: Character,
            relation_type: str,
            transition: str,
            from_ch: int,
            to_ch: int | None,
            confidence: float,
            evidence_id: str,
            excerpt: str,
            idem_suffix: str,
            package_hash: str,
        ) -> RelationshipObservation:
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
            )
            session.add(build)
            session.flush()
            cand = RelationshipObservationCandidate(
                owner_id=owner.id,
                novel_id=novel.id,
                analysis_version_id=version.id,
                build_run_id=build.id,
                source_judgment_id=kjudg.id,
                source_relation_candidate_id=kcand.id,
                source_character_id=src.id,
                target_character_id=tgt.id,
                relation_type=relation_type,
                package_hash=package_hash,
                package_snapshot={},
                recall_signals={},
                evidence_refs=[evidence_id],
                status="accepted",
            )
            session.add(cand)
            session.flush()
            judgment = RelationshipObservationJudgment(
                owner_id=owner.id,
                novel_id=novel.id,
                analysis_version_id=version.id,
                build_run_id=build.id,
                candidate_id=cand.id,
                prompt_hash=HEX64,
                schema_hash=HEX64,
                policy_hash=HEX64,
                model_name="test",
                model_lineage={},
                relation_type=relation_type,
                transition=transition,
                confidence=confidence,
                valid_from_evidence_id=evidence_id,
                supporting_evidence_ids=[evidence_id],
                structured_output={},
                risk_flags=[],
                status="accepted",
                gate_status="accepted",
            )
            session.add(judgment)
            session.flush()
            obs = RelationshipObservation(
                owner_id=owner.id,
                novel_id=novel.id,
                analysis_version_id=version.id,
                build_run_id=build.id,
                candidate_id=cand.id,
                judgment_id=judgment.id,
                source_judgment_id=kjudg.id,
                source_character_id=src.id,
                target_character_id=tgt.id,
                relation_type=relation_type,
                transition=transition,
                status="accepted",
                valid_from_chapter=from_ch,
                valid_from_narrative_index=0,
                valid_to_chapter=to_ch,
                valid_to_narrative_index=0 if to_ch is not None else None,
                valid_from_evidence_id=evidence_id,
                confidence=confidence,
                evidence_checksum=HEX64,
                observation_checksum=HEX64_B,
                prompt_hash=HEX64,
                schema_hash=HEX64,
                policy_hash=HEX64,
                model_lineage={},
                idempotency_key=f"idem-{idem_suffix}-{version.id}-{src.id}-{tgt.id}",
            )
            session.add(obs)
            session.flush()
            session.add(
                RelationshipEvidenceLink(
                    observation_id=obs.id,
                    owner_id=owner.id,
                    novel_id=novel.id,
                    analysis_version_id=version.id,
                    evidence_id=evidence_id,
                    chapter_id=chapters[from_ch - 1].id,
                    source_start=0,
                    source_end=12,
                    content_hash=HEX64,
                    excerpt=excerpt,
                    sort_order=0,
                )
            )
            session.flush()
            return obs

        early = _add_obs(
            version=v1,
            src=alice,
            tgt=bob,
            relation_type="ally",
            transition="establish",
            from_ch=1,
            to_ch=None,
            confidence=0.92,
            evidence_id="e-early",
            excerpt="Alice and Bob ally at dawn",
            idem_suffix="early",
            package_hash=HEX64,
        )
        # Future observation (chapter 3) — must not leak before cutoff.
        future = None
        if with_future:
            future = _add_obs(
                version=v1,
                src=alice,
                tgt=carol,
                relation_type="enemy",
                transition="establish",
                from_ch=3,
                to_ch=None,
                confidence=0.93,
                evidence_id="e-future",
                excerpt="SECRET_FUTURE_ENEMY_WITH_CarolFuture",
                idem_suffix="future",
                package_hash=HEX64_B,
            )
        # Transition chain: ally ends at chapter 2, becomes enemy.
        end_ally = _add_obs(
            version=v1,
            src=alice,
            tgt=bob,
            relation_type="ally",
            transition="end",
            from_ch=2,
            to_ch=None,
            confidence=0.9,
            evidence_id="e-end-ally",
            excerpt="alliance ends",
            idem_suffix="end-ally",
            package_hash=HEX64_C,
        )
        enemy = _add_obs(
            version=v1,
            src=alice,
            tgt=bob,
            relation_type="enemy",
            transition="establish",
            from_ch=2,
            to_ch=None,
            confidence=0.91,
            evidence_id="e-enemy",
            excerpt="Alice and Bob become enemies",
            idem_suffix="enemy",
            package_hash=HEX64_D,
        )
        # Candidate version only: distinct edge (mentor).
        candidate_obs = _add_obs(
            version=v2,
            src=alice,
            tgt=bob,
            relation_type="mentor",
            transition="establish",
            from_ch=1,
            to_ch=None,
            confidence=0.88,
            evidence_id="e-cand",
            excerpt="CANDIDATE_ONLY_MENTOR",
            idem_suffix="cand",
            package_hash="e" * 64,
        )

        session.commit()
        return {
            "owner_id": owner.id,
            "other_id": other.id,
            "novel_id": novel.id,
            "v1_id": v1.id,
            "v2_id": v2.id,
            "alice_id": alice.id,
            "bob_id": bob.id,
            "carol_id": carol.id,
            "chapter1_id": chapters[0].id,
            "chapter2_id": chapters[1].id,
            "chapter3_id": chapters[2].id,
            "early_obs_id": early.id,
            "future_obs_id": future.id if future else None,
            "end_ally_obs_id": end_ally.id,
            "enemy_obs_id": enemy.id,
            "candidate_obs_id": candidate_obs.id,
        }


async def _async_session(empty_postgres: str):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    aengine = create_async_engine(_async_url(empty_postgres))
    factory = async_sessionmaker(aengine, expire_on_commit=False)
    return aengine, factory


@pytest.mark.asyncio
async def test_query_spoiler_visible_set_first_and_fold(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipGraphQueryService()
    async with factory() as db:
        novel = await db.get(Novel, ids["novel_id"])
        assert novel is not None

        # No progress → first chapter only (D-09).
        envelope = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
        )
        assert envelope is not None
        assert envelope.cutoff_chapter == 1
        assert envelope.through_chapter == 1
        assert envelope.version_id == ids["v1_id"]
        assert envelope.source == RelationshipVersionSource.ACTIVE
        names = {n.name for n in envelope.nodes}
        assert "Alice" in names and "Bob" in names
        assert "CarolFuture" not in names
        types = {e.relation_type.value for e in envelope.edges}
        assert types == {"ally"}
        payload = envelope.model_dump_json()
        assert "CarolFuture" not in payload
        assert "SECRET_FUTURE" not in payload
        assert "LEGACY_SECRET" not in payload
        assert "CANDIDATE_ONLY" not in payload
        assert "enemy" not in payload  # enemy starts chapter 2

        # After chapter 2 progress: ally ended, enemy established; future still hidden.
        novel.reading_progress = {"chapter_id": ids["chapter2_id"]}
        await db.commit()
        await db.refresh(novel)

        mid = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
        )
        assert mid is not None
        assert mid.cutoff_chapter == 2
        mid_types = {e.relation_type.value for e in mid.edges}
        assert mid_types == {"enemy"}
        assert "CarolFuture" not in mid.model_dump_json()
        assert mid.counts.edges == 1
        assert set(mid.available_character_ids) == {ids["alice_id"], ids["bob_id"]}

        # Full-book denied without preference.
        denied = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            request_full_book=True,
        )
        assert denied is not None
        assert denied.full_book is False
        assert "CarolFuture" not in denied.model_dump_json()

        novel.reading_progress = {
            "chapter_id": ids["chapter2_id"],
            "timeline_full_book": True,
        }
        await db.commit()
        await db.refresh(novel)
        allowed = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            request_full_book=True,
        )
        assert allowed is not None
        assert allowed.full_book is True
        assert "CarolFuture" in {n.name for n in allowed.nodes}
        assert any(e.relation_type.value == "enemy" for e in allowed.edges)

    await aengine.dispose()


@pytest.mark.asyncio
async def test_version_source_isolation_active_vs_candidate(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipGraphQueryService()
    async with factory() as db:
        novel = await db.get(Novel, ids["novel_id"])
        assert novel is not None
        novel.reading_progress = {
            "chapter_id": ids["chapter3_id"],
            "timeline_full_book": True,
        }
        # Running candidate run for v2.
        db.add(
            AnalysisRun(
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                active_key="active",
                status="running",
                version_id=ids["v2_id"],
                progress={"completed_chapters": 3},
            )
        )
        await db.commit()
        await db.refresh(novel)

        active = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            request_full_book=True,
        )
        candidate = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.RUNNING_CANDIDATE,
            request_full_book=True,
        )
        assert active is not None and candidate is not None
        assert active.version_id == ids["v1_id"]
        assert candidate.version_id == ids["v2_id"]
        active_types = {e.relation_type.value for e in active.edges}
        cand_types = {e.relation_type.value for e in candidate.edges}
        assert "mentor" not in active_types
        assert cand_types == {"mentor"}
        assert "CANDIDATE_ONLY" not in active.model_dump_json()
        assert "SECRET_FUTURE" not in candidate.model_dump_json()

        # History: explicit v1 proven inside owner/novel.
        history = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.HISTORY,
            version_id=ids["v1_id"],
            request_full_book=True,
        )
        assert history is not None
        assert history.version_id == ids["v1_id"]
        assert history.source == RelationshipVersionSource.HISTORY

        # Cross-owner proof fails closed.
        missing = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["other_id"],
            source=RelationshipVersionSource.ACTIVE,
        )
        assert missing is None

        # Foreign version id fails.
        foreign = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.HISTORY,
            version_id=999999,
        )
        assert foreign is None

    await aengine.dispose()


@pytest.mark.asyncio
async def test_legacy_character_relation_does_not_change_checksum(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipGraphQueryService()
    async with factory() as db:
        novel = await db.get(Novel, ids["novel_id"])
        assert novel is not None
        first = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
        )
        assert first is not None
        checksum1 = hashlib.sha256(first.model_dump_json().encode()).hexdigest()

        # Add more legacy noise.
        db.add(
            CharacterRelation(
                novel_id=ids["novel_id"],
                source_character_id=ids["alice_id"],
                target_character_id=ids["carol_id"],
                relation_type="lover",
                strength=10,
                description="MORE_LEGACY_NOISE",
            )
        )
        await db.commit()

        second = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
        )
        assert second is not None
        # Drop generated_at for stable compare.
        d1 = json.loads(first.model_dump_json())
        d2 = json.loads(second.model_dump_json())
        d1.pop("generated_at", None)
        d2.pop("generated_at", None)
        assert d1 == d2
        assert "MORE_LEGACY" not in second.model_dump_json()
        checksum2 = hashlib.sha256(
            json.dumps(d2, sort_keys=True).encode()
        ).hexdigest()
        assert checksum2 == hashlib.sha256(
            json.dumps(d1, sort_keys=True).encode()
        ).hexdigest()
        assert checksum1  # sanity

    await aengine.dispose()


@pytest.mark.asyncio
async def test_filters_required_over_cap_returns_empty_elements(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine, with_future=False)

    # Seed enough synthetic characters/edges to exceed hard caps.
    with Session(engine) as session:
        owner_id = ids["owner_id"]
        novel_id = ids["novel_id"]
        version_id = ids["v1_id"]
        chapter_id = ids["chapter1_id"]
        from app.models.knowledge import (
            KnowledgeExtractionRun,
            KnowledgeRelationCandidate,
            KnowledgeRelationJudgment,
        )

        session.execute(
            select(KnowledgeExtractionRun).where(
                KnowledgeExtractionRun.novel_id == novel_id
            )
        ).scalar_one()
        kcand = session.execute(
            select(KnowledgeRelationCandidate).where(
                KnowledgeRelationCandidate.novel_id == novel_id
            )
        ).scalar_one()
        kjudg = session.execute(
            select(KnowledgeRelationJudgment).where(
                KnowledgeRelationJudgment.novel_id == novel_id
            )
        ).scalar_one()

        chars = []
        # HARD_NODE_CAP is 500; create 502 characters so any edges between them exceed nodes.
        for i in range(HARD_NODE_CAP + 2):
            c = Character(novel_id=novel_id, name=f"Bulk{i}", role="minor")
            session.add(c)
            chars.append(c)
        session.flush()

        build = RelationshipBuildRun(
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=version_id,
            status="completed",
            checkpoint={},
            progress={},
            prompt_hash=HEX64,
            schema_hash=HEX64,
            policy_hash=HEX64,
            decoding_hash=HEX64,
            model_lineage={},
        )
        session.add(build)
        session.flush()

        # Create edges as a star from chars[0] to all others → node_count = HARD_NODE_CAP+2
        for i, target in enumerate(chars[1:], start=1):
            cand = RelationshipObservationCandidate(
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=version_id,
                build_run_id=build.id,
                source_judgment_id=kjudg.id,
                source_relation_candidate_id=kcand.id,
                source_character_id=chars[0].id,
                target_character_id=target.id,
                relation_type="ally",
                package_hash=hashlib.sha256(f"bulk-{i}".encode()).hexdigest(),
                package_snapshot={},
                recall_signals={},
                evidence_refs=[f"e-bulk-{i}"],
                status="accepted",
            )
            session.add(cand)
            session.flush()
            judgment = RelationshipObservationJudgment(
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=version_id,
                build_run_id=build.id,
                candidate_id=cand.id,
                prompt_hash=HEX64,
                schema_hash=HEX64,
                policy_hash=HEX64,
                model_name="test",
                model_lineage={},
                relation_type="ally",
                transition="establish",
                confidence=0.9,
                valid_from_evidence_id=f"e-bulk-{i}",
                supporting_evidence_ids=[f"e-bulk-{i}"],
                structured_output={},
                risk_flags=[],
                status="accepted",
                gate_status="accepted",
            )
            session.add(judgment)
            session.flush()
            obs = RelationshipObservation(
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=version_id,
                build_run_id=build.id,
                candidate_id=cand.id,
                judgment_id=judgment.id,
                source_judgment_id=kjudg.id,
                source_character_id=chars[0].id,
                target_character_id=target.id,
                relation_type="ally",
                transition="establish",
                status="accepted",
                valid_from_chapter=1,
                valid_from_narrative_index=0,
                valid_from_evidence_id=f"e-bulk-{i}",
                confidence=0.9,
                evidence_checksum=HEX64,
                observation_checksum=HEX64_B,
                prompt_hash=HEX64,
                schema_hash=HEX64,
                policy_hash=HEX64,
                model_lineage={},
                idempotency_key=f"idem-bulk-{i}-{version_id}",
            )
            session.add(obs)
            session.flush()
            session.add(
                RelationshipEvidenceLink(
                    observation_id=obs.id,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    analysis_version_id=version_id,
                    evidence_id=f"e-bulk-{i}",
                    chapter_id=chapter_id,
                    source_start=0,
                    source_end=5,
                    content_hash=HEX64,
                    excerpt=f"bulk-{i}",
                    sort_order=0,
                )
            )
        session.commit()
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipGraphQueryService()
    async with factory() as db:
        novel = await db.get(Novel, ids["novel_id"])
        assert novel is not None
        novel.reading_progress = {"timeline_full_book": True}
        await db.commit()
        await db.refresh(novel)

        envelope = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            request_full_book=True,
        )
        assert envelope is not None
        assert envelope.degradation.mode.value == "filters_required"
        assert envelope.nodes == []
        assert envelope.edges == []
        assert envelope.counts.nodes > HARD_NODE_CAP
        assert envelope.degradation.node_count > HARD_NODE_CAP
        assert envelope.degradation.hard_node_cap == HARD_NODE_CAP
        assert envelope.degradation.hard_edge_cap == HARD_EDGE_CAP
        # Filters metadata remains spoiler-safe (from same visible set).
        assert envelope.available_character_ids
        assert envelope.available_relation_types

    await aengine.dispose()


@pytest.mark.asyncio
async def test_fold_before_after_without_mutating_rows(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipGraphQueryService()
    async with factory() as db:
        novel = await db.get(Novel, ids["novel_id"])
        assert novel is not None
        novel.reading_progress = {"timeline_full_book": True}
        await db.commit()
        await db.refresh(novel)

        before = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            through_chapter=1,
            request_full_book=True,
        )
        after = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            through_chapter=2,
            request_full_book=True,
        )
        assert before is not None and after is not None
        assert {e.relation_type.value for e in before.edges} == {"ally"}
        assert {e.relation_type.value for e in after.edges} >= {"enemy"}
        assert not any(e.relation_type.value == "ally" for e in after.edges)

        # Rows unchanged after fold.
        obs = (
            await db.execute(
                select(RelationshipObservation).where(
                    RelationshipObservation.id == ids["early_obs_id"]
                )
            )
        ).scalar_one()
        assert obs.relation_type == "ally"
        assert obs.transition == "establish"

    await aengine.dispose()


@pytest.mark.asyncio
async def test_override_supersession_keeps_prior_bytes_and_relink_ambiguous(
    empty_postgres: str, require_postgres: None
):
    from app.schemas.relationship import (
        OverrideStatus,
        RelationshipOverrideCreate,
        RelationshipOverrideField,
    )
    from app.services.relationships.overrides import relationship_override_service

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine, with_future=False)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    async with factory() as db:
        key = logical_relationship_key(ids["alice_id"], ids["bob_id"], "ally")
        first = await relationship_override_service.append_relationship_override(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            payload=RelationshipOverrideCreate(
                novel_id=ids["novel_id"],
                analysis_version_id=ids["v1_id"],
                observation_id=ids["early_obs_id"],
                logical_relationship_key=key,
                field_name=RelationshipOverrideField.RELATION_TYPE,
                value={"relation_type": "mentor"},
                author="owner@example.com",
                reason="initial tone",
                evidence_signature=HEX64,
            ),
        )
        await db.commit()
        prior_id = first.id
        prior_row = await db.get(RelationshipOverride, prior_id)
        prior_bytes = (
            prior_row.status,
            prior_row.reason,
            prior_row.author,
            dict(prior_row.value),
            prior_row.evidence_signature,
            prior_row.supersedes_id,
        )

        second = await relationship_override_service.append_relationship_override(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            payload=RelationshipOverrideCreate(
                novel_id=ids["novel_id"],
                analysis_version_id=ids["v1_id"],
                observation_id=ids["early_obs_id"],
                logical_relationship_key=key,
                field_name=RelationshipOverrideField.RELATION_TYPE,
                value={"relation_type": "family"},
                author="owner@example.com",
                reason="supersede tone",
                evidence_signature=HEX64_B,
            ),
        )
        await db.commit()
        assert second.supersedes_id == prior_id

        prior_row = await db.get(RelationshipOverride, prior_id)
        assert (
            prior_row.status,
            prior_row.reason,
            prior_row.author,
            dict(prior_row.value),
            prior_row.evidence_signature,
            prior_row.supersedes_id,
        ) == prior_bytes

        novel = await db.get(Novel, ids["novel_id"])
        novel.reading_progress = {"timeline_full_book": True}
        await db.commit()
        await db.refresh(novel)
        svc = RelationshipGraphQueryService()
        graph = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            through_chapter=1,
            request_full_book=True,
        )
        assert graph is not None
        assert any(e.relation_type.value == "family" for e in graph.edges)
        assert any(e.provenance.value == "manual" for e in graph.edges)

        # Zero matches on v2 → needs_relink
        relink0 = await relationship_override_service.relink_override_to_version(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            override_id=second.id,
            target_version_id=ids["v2_id"],
            override_kind="relationship",
        )
        await db.commit()
        assert relink0.status == OverrideStatus.NEEDS_RELINK

        # Multiple observations share evidence_checksum HEX64 on v1 → needs_relink
        multi = await relationship_override_service.append_relationship_override(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            payload=RelationshipOverrideCreate(
                novel_id=ids["novel_id"],
                analysis_version_id=ids["v1_id"],
                observation_id=ids["early_obs_id"],
                logical_relationship_key=key,
                field_name=RelationshipOverrideField.RELATION_TYPE,
                value={"relation_type": "romantic"},
                author="owner@example.com",
                reason="shared sig",
                evidence_signature=HEX64,
            ),
        )
        await db.commit()
        relink_multi = await relationship_override_service.relink_override_to_version(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            override_id=multi.id,
            target_version_id=ids["v1_id"],
            override_kind="relationship",
        )
        await db.commit()
        assert relink_multi.status == OverrideStatus.NEEDS_RELINK
        assert (relink_multi.provenance or {}).get("match_count", 0) != 1

    await aengine.dispose()


@pytest.mark.asyncio
async def test_api_router_mounted_and_cross_owner_404(
    empty_postgres: str, require_postgres: None
):
    from httpx import ASGITransport, AsyncClient
    from app.core.database import get_db
    from app.core.security import require_user
    from app.main import app
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine, with_future=False)
    engine.dispose()

    paths = app.openapi()["paths"]
    assert "/api/relationships/{novel_id}/graph" in paths
    assert sum(1 for p in paths if p == "/api/relationships/{novel_id}/graph") == 1

    aengine = create_async_engine(_async_url(empty_postgres))
    factory = async_sessionmaker(aengine, expire_on_commit=False)

    async def _override_db():
        async with factory() as session:
            yield session

    owner = User(
        id=ids["owner_id"],
        username="rel_api_owner",
        email="rel_api@example.com",
        hashed_password="hash",
        is_active=True,
        is_superuser=False,
    )
    other = User(
        id=ids["other_id"],
        username="rel_api_other",
        email="rel_other@example.com",
        hashed_password="hash",
        is_active=True,
        is_superuser=False,
    )

    async def _as_other():
        return other

    async def _as_owner():
        return owner

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_user] = _as_other
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/relationships/{ids['novel_id']}/graph")
            assert resp.status_code == 404
            body = resp.json()
            detail = str(body.get("detail", ""))
            assert str(ids["v1_id"]) not in detail
            assert "Relationship Graph Novel" not in detail

        app.dependency_overrides[require_user] = _as_owner
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ok = await client.get(f"/api/relationships/{ids['novel_id']}/graph")
            assert ok.status_code == 200
            data = ok.json()
            assert data["version_id"] == ids["v1_id"]
            assert "degradation" in data
            assert "cutoff_chapter" in data
            assert data["source"] == "active"
            assert "provenance" in data["edges"][0] if data["edges"] else True

            bad = await client.get(
                f"/api/relationships/{ids['novel_id']}/graph",
                params={"source": "history", "version_id": 999999},
            )
            assert bad.status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_user, None)
        await aengine.dispose()
