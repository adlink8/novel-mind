"""PostgreSQL integration tests for Phase 09 relationship observation pipeline."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.analysis import AnalysisVersion
from app.models.character import Character
from app.models.knowledge import (
    KnowledgeEntityCandidate,
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.models.novel import Chapter, Novel
from app.models.relationship import (
    RelationshipEvidenceLink,
    RelationshipObservation,
    RelationshipObservationCandidate,
    RelationshipObservationJudgment,
)
from app.models.text_chunk import TextChunk
from app.models.user import User
from app.services.relationships.gates import AUTO_ACCEPT_THRESHOLD
from app.services.relationships.worker import RelationshipObservationWorker
from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64


def _seed_pipeline_graph(engine, *, relation_type: str = "ally") -> dict[str, Any]:
    """Seed owner/novel/version/characters/knowledge accepted judgment chain."""

    with Session(engine) as session:
        user = User(
            username="rel_pipe_owner",
            email="rel_pipe@example.com",
            hashed_password="hash",
        )
        session.add(user)
        session.flush()

        novel = Novel(title="Pipeline Novel", owner_id=user.id)
        session.add(novel)
        session.flush()

        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="Chapter 1",
            content="Alice and Bob swore an alliance on the bridge.",
            word_count=40,
        )
        session.add(chapter)
        session.flush()

        chunk = TextChunk(
            novel_id=novel.id,
            chapter_id=chapter.id,
            chunk_index=0,
            content=chapter.content,
            chunk_type="narration",
            metadata_json={"characters": ["Alice", "Bob"]},
            word_count=40,
            embedding_status="embedded",
        )
        session.add(chunk)
        session.flush()

        alice = Character(novel_id=novel.id, name="Alice", role="protagonist")
        bob = Character(novel_id=novel.id, name="Bob", role="supporting")
        session.add_all([alice, bob])
        session.flush()

        version = AnalysisVersion(
            owner_id=user.id,
            novel_id=novel.id,
            version_key="v1",
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
        session.add(version)
        session.flush()

        run = KnowledgeExtractionRun(
            owner_id=user.id,
            novel_id=novel.id,
            run_name="rel-source",
            domain_profile="fiction",
            ontology_profile="fiction.v1",
            status="completed",
        )
        session.add(run)
        session.flush()

        evidence = KnowledgeEvidenceRef(
            owner_id=user.id,
            novel_id=novel.id,
            run_id=run.id,
            ref_key=f"ev-chunk-{chunk.id}",
            source_type="text_chunk",
            text_chunk_id=chunk.id,
            chapter_id=chapter.id,
            excerpt="Alice and Bob swore an alliance",
            char_start=0,
            char_end=len(chapter.content),
        )
        session.add(evidence)
        session.flush()

        ent_alice = KnowledgeEntityCandidate(
            owner_id=user.id,
            novel_id=novel.id,
            run_id=run.id,
            canonical_name="Alice",
            aliases=[],
            domain_profile="fiction",
            entity_type="character",
            evidence_refs=[evidence.ref_key],
            source_refs=[evidence.ref_key],
            confidence=0.9,
            status="accepted",
        )
        ent_bob = KnowledgeEntityCandidate(
            owner_id=user.id,
            novel_id=novel.id,
            run_id=run.id,
            canonical_name="Bob",
            aliases=[],
            domain_profile="fiction",
            entity_type="character",
            evidence_refs=[evidence.ref_key],
            source_refs=[evidence.ref_key],
            confidence=0.9,
            status="accepted",
        )
        session.add_all([ent_alice, ent_bob])
        session.flush()

        rel_cand = KnowledgeRelationCandidate(
            owner_id=user.id,
            novel_id=novel.id,
            run_id=run.id,
            domain_profile="fiction",
            relation_type=relation_type,
            source_kind="entity_candidate",
            source_id=ent_alice.id,
            target_kind="entity_candidate",
            target_id=ent_bob.id,
            recall_signals={"adjacency": {"same_chapter": True}, "vector": {"score": 0.88}},
            package_snapshot={"allowed_evidence_ids": [evidence.ref_key]},
            evidence_refs=[evidence.ref_key],
            status="accepted",
        )
        session.add(rel_cand)
        session.flush()

        judgment = KnowledgeRelationJudgment(
            owner_id=user.id,
            novel_id=novel.id,
            run_id=run.id,
            relation_candidate_id=rel_cand.id,
            prompt_version="knowledge-relation-judge.v1",
            model_name="test/model",
            relation_type=relation_type,
            confidence=0.92,
            evidence_refs=[evidence.ref_key],
            rationale="supported by chapter text",
            risk_flags=[],
            raw_output={},
            structured_output={"relation_type": relation_type},
            status="accepted",
            gate_status="accepted",
        )
        session.add(judgment)
        session.flush()

        ids = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "analysis_version_id": version.id,
            "chapter_id": chapter.id,
            "chunk_id": chunk.id,
            "alice_id": alice.id,
            "bob_id": bob.id,
            "source_judgment_id": judgment.id,
            "evidence_id": evidence.ref_key,
            "relation_type": relation_type,
        }
        session.commit()
        return ids


def _seed_non_edge(engine, ids: dict[str, Any], relation_type: str) -> int:
    with Session(engine) as session:
        run = session.execute(
            select(KnowledgeExtractionRun).where(
                KnowledgeExtractionRun.novel_id == ids["novel_id"]
            )
        ).scalar_one()
        rel_cand = KnowledgeRelationCandidate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            run_id=run.id,
            domain_profile="fiction",
            relation_type=relation_type,
            source_kind="entity_candidate",
            source_id=1,
            target_kind="entity_candidate",
            target_id=2,
            recall_signals={"same_entity_score": 0.99},
            package_snapshot={"allowed_evidence_ids": [ids["evidence_id"]]},
            evidence_refs=[ids["evidence_id"]],
            status="accepted",
        )
        session.add(rel_cand)
        session.flush()
        judgment = KnowledgeRelationJudgment(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            run_id=run.id,
            relation_candidate_id=rel_cand.id,
            prompt_version="knowledge-relation-judge.v1",
            model_name="test/model",
            relation_type=relation_type,
            confidence=0.99,
            evidence_refs=[ids["evidence_id"]],
            rationale="non edge",
            risk_flags=[],
            raw_output={},
            structured_output={},
            status="accepted",
            gate_status="accepted",
        )
        session.add(judgment)
        session.flush()
        jid = judgment.id
        session.commit()
        return jid


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


def _accept_payload(ids: dict[str, Any], *, confidence: float = 0.91) -> dict[str, Any]:
    candidate_key = (
        f"sj:{ids['source_judgment_id']}:sc:{ids['alice_id']}:"
        f"tc:{ids['bob_id']}:rt:ally"
    )
    payload = {
        "schema_version": "relationship-semantic-judgment.v1",
        "candidate_key": candidate_key,
        "source_ref": f"character:{ids['alice_id']}",
        "target_ref": f"character:{ids['bob_id']}",
        "relation_type": "ally",
        "transition": "establish",
        "valid_from_evidence_id": ids["evidence_id"],
        "valid_to_evidence_id": None,
        "supporting_evidence_ids": [ids["evidence_id"]],
        "confidence": confidence,
        "rationale": "alliance sworn",
        "risk_flags": [],
    }
    return payload


@pytest.mark.asyncio
async def test_pipeline_accepts_fiction_edge_and_is_idempotent(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_pipeline_graph(engine)
    engine.dispose()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    aengine = create_async_engine(_async_url(empty_postgres))
    SessionLocal = async_sessionmaker(aengine, expire_on_commit=False)

    base = _accept_payload(ids)
    det = {
        str(ids["source_judgment_id"]): base,
        base["candidate_key"]: base,
    }

    worker = RelationshipObservationWorker(model_name="test/model")

    async with SessionLocal() as db:
        result1 = await worker.run(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            analysis_version_id=ids["analysis_version_id"],
            deterministic_outputs=det,
        )
        await db.commit()

        assert result1.status == "completed"
        assert result1.accepted_count == 1
        assert result1.provider_calls == 0  # deterministic call_skipped
        assert result1.call_skipped >= 1

        obs = (
            await db.execute(select(RelationshipObservation))
        ).scalars().all()
        assert len(obs) == 1
        assert obs[0].status == "accepted"
        assert obs[0].relation_type == "ally"
        assert obs[0].source_character_id == ids["alice_id"]
        assert obs[0].target_character_id == ids["bob_id"]
        assert obs[0].source_judgment_id == ids["source_judgment_id"]
        assert obs[0].evidence_checksum
        assert obs[0].idempotency_key
        checksum_before = obs[0].observation_checksum

        links = (
            await db.execute(select(RelationshipEvidenceLink))
        ).scalars().all()
        assert len(links) == 1
        assert links[0].evidence_id == ids["evidence_id"]
        assert links[0].content_hash

        # Repeat run: zero new observations / provider calls for same version.
        result2 = await worker.run(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            analysis_version_id=ids["analysis_version_id"],
            deterministic_outputs=det,
        )
        await db.commit()
        obs2 = (
            await db.execute(select(RelationshipObservation))
        ).scalars().all()
        assert len(obs2) == 1
        assert obs2[0].observation_checksum == checksum_before
        assert result2.status == "completed"

    await aengine.dispose()


@pytest.mark.asyncio
async def test_pipeline_rejects_non_edges_and_threshold_review(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_pipeline_graph(engine, relation_type="ally")
    _seed_non_edge(engine, ids, "same_entity")
    _seed_non_edge(engine, ids, "causes")
    _seed_non_edge(engine, ids, "precedes")
    engine.dispose()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    aengine = create_async_engine(_async_url(empty_postgres))
    SessionLocal = async_sessionmaker(aengine, expire_on_commit=False)

    review_payload = _accept_payload(ids, confidence=0.70)
    det = {
        str(ids["source_judgment_id"]): review_payload,
        review_payload["candidate_key"]: review_payload,
    }
    worker = RelationshipObservationWorker(model_name="test/model")

    async with SessionLocal() as db:
        result = await worker.run(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            analysis_version_id=ids["analysis_version_id"],
            deterministic_outputs=det,
        )
        await db.commit()

        assert result.status == "completed"
        # only ally edge is a candidate; same_entity/causes/precedes never package
        assert result.accepted_count == 0
        assert result.review_count == 1
        assert result.identity_reviews >= 1

        obs = (await db.execute(select(RelationshipObservation))).scalars().all()
        assert len(obs) == 0

        candidates = (
            await db.execute(select(RelationshipObservationCandidate))
        ).scalars().all()
        assert len(candidates) == 1
        assert candidates[0].status == "needs_human_review"
        assert candidates[0].relation_type == "ally"
        # Recall metadata persisted separately; no observation written from vector score.
        assert "vector" in (candidates[0].recall_signals or {})

    await aengine.dispose()


@pytest.mark.asyncio
async def test_pipeline_forged_evidence_and_revoked_source_fail_closed(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_pipeline_graph(engine)
    engine.dispose()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    aengine = create_async_engine(_async_url(empty_postgres))
    SessionLocal = async_sessionmaker(aengine, expire_on_commit=False)

    forged = _accept_payload(ids, confidence=0.99)
    forged["valid_from_evidence_id"] = "ev-forged-999"
    forged["supporting_evidence_ids"] = ["ev-forged-999"]
    forged["rationale"] = "forged"
    det = {str(ids["source_judgment_id"]): forged, forged["candidate_key"]: forged}
    worker = RelationshipObservationWorker(model_name="test/model")

    async with SessionLocal() as db:
        result = await worker.run(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            analysis_version_id=ids["analysis_version_id"],
            deterministic_outputs=det,
        )
        await db.commit()
        assert result.accepted_count == 0
        obs = (await db.execute(select(RelationshipObservation))).scalars().all()
        assert len(obs) == 0
        judgments = (
            await db.execute(select(RelationshipObservationJudgment))
        ).scalars().all()
        assert judgments
        assert judgments[0].status in {
            "rejected",
            "schema_failed",
            "evidence_failed",
        }

        # Revoke source judgment and re-run with valid high confidence.
        src = await db.get(KnowledgeRelationJudgment, ids["source_judgment_id"])
        src.status = "rejected"
        src.gate_status = "rejected"
        await db.commit()

    # New build with valid payload but revoked source.
    valid = _accept_payload(ids, confidence=AUTO_ACCEPT_THRESHOLD)
    det2 = {str(ids["source_judgment_id"]): valid, valid["candidate_key"]: valid}
    async with SessionLocal() as db:
        result2 = await worker.run(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            analysis_version_id=ids["analysis_version_id"],
            deterministic_outputs=det2,
        )
        await db.commit()
        # Source selection filters non-accepted, so zero candidates/accepted.
        assert result2.accepted_count == 0
        obs = (await db.execute(select(RelationshipObservation))).scalars().all()
        assert len(obs) == 0

    await aengine.dispose()


@pytest.mark.asyncio
async def test_pipeline_new_analysis_version_creates_distinct_observations(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_pipeline_graph(engine)
    with Session(engine) as session:
        v2 = AnalysisVersion(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_key="v2",
            status="candidate",
            source_snapshot_hash="d" * 64,
            hierarchy_build_id="build-v2",
            hierarchy_checksum="e" * 64,
            prompt_hash=HEX64,
            schema_hash=HEX64_B,
            model_lineage={},
            decoding_hash=HEX64_C,
            config_hash=HEX64,
            price_snapshot={},
            manifest={},
        )
        session.add(v2)
        session.flush()
        v2_id = v2.id
        session.commit()
    engine.dispose()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    aengine = create_async_engine(_async_url(empty_postgres))
    SessionLocal = async_sessionmaker(aengine, expire_on_commit=False)

    payload = _accept_payload(ids, confidence=0.9)
    det = {str(ids["source_judgment_id"]): payload, payload["candidate_key"]: payload}
    worker = RelationshipObservationWorker(model_name="test/model")

    async with SessionLocal() as db:
        r1 = await worker.run(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            analysis_version_id=ids["analysis_version_id"],
            deterministic_outputs=det,
        )
        await db.commit()
        assert r1.accepted_count == 1

        # Version 2 uses same source judgment but distinct package (version id in hash).
        r2 = await worker.run(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            analysis_version_id=v2_id,
            deterministic_outputs=det,
        )
        await db.commit()
        assert r2.accepted_count == 1

        obs = (await db.execute(select(RelationshipObservation))).scalars().all()
        assert len(obs) == 2
        versions = {o.analysis_version_id for o in obs}
        assert versions == {ids["analysis_version_id"], v2_id}
        keys = {o.idempotency_key for o in obs}
        assert len(keys) == 2

    await aengine.dispose()
