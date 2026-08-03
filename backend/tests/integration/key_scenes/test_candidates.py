"""Phase 31-02 Key Scene candidate API integration tests (REQ-VIS-02/06).

Covers the 31-VALIDATION.md integration matrix:
- owner, novel, source snapshot, spoiler cutoff and candidate-only gates all
  hold at the API boundary; cross-owner access fails closed (no owner leak);
- generation exposes evidence refs, salience reasons, diversity keys and
  non-authoritative ``speaker_dialogue_signal`` metadata;
- the advisory heuristic signal is diagnostic candidate metadata only: its
  offsets/confidence/warnings never populate evidence ranges and never become
  citation/Canon/approval authority;
- identical generation replays the same immutable set; conflicting retries and
  max-candidate budget gates fail closed or bound the set;
- generation never promotes anything to Canon and never rewrites source text.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.security import create_access_token, hash_password
from app.models.key_scene import (
    SceneCandidate,
    SceneCandidateSet,
    SceneEvidenceRange,
)
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.schemas.key_scene import SceneCoordinates
from app.services.key_scenes.boundaries import (
    ChapterRecord,
    compute_source_snapshot_hash,
    detect_chapter_boundaries,
)
from app.services.key_scenes.candidates import (
    CandidateGenerationInput,
    CandidateService,
)

pytestmark = pytest.mark.integration

# Single-paragraph chapters so scene boundaries are deterministic (one scene).
CH_ACTION = (
    "Arin drew his sword as the rain fell hard across the courtyard walls. "
    '"We attack at dawn!" he said. Mara drew her sword and charged. '
    "The enemy banners would rise with the sun and there would be no going back! "
    "Torches guttered low across the courtyard as the attack exploded."
)
CH_QUIET = (
    "It was a quiet night on the harbor. Arin wept quietly by the rail and "
    "remembered the grief of the long winter. She watched the moon and thought "
    "of everyone they had lost, in a calm that hurt more than any battle."
)
CH_AMBIGUOUS = (
    "Arin walked into the hall. He sat down. Nothing much happened and no one "
    "spoke as the minutes passed."
)

HEX64 = "a" * 64


def _scene_coordinates(chapter_number: int) -> dict[str, Any]:
    if chapter_number == 1:
        return {
            "cast": ["arin", "mara"],
            "place": "courtyard",
            "time": "night",
            "pov": "arin",
        }
    if chapter_number == 2:
        return {
            "cast": ["arin"],
            "place": "harbor",
            "time": "night",
            "pov": "arin",
        }
    return {
        "cast": ["arin"],
        "place": "hall",
        "time": "evening",
        "pov": "arin",
    }


def _seed_owner(
    sync_url: str, *, suffix: str, contents: tuple[str, ...] | None = None
) -> dict[str, Any]:
    contents = contents or (CH_ACTION, CH_QUIET, CH_AMBIGUOUS)
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"ks_{suffix}",
            email=f"ks_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"KS Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=len(contents),
            word_count=sum(len(c) for c in contents),
        )
        session.add(novel)
        session.flush()
        chapter_ids: list[int] = []
        for i, content in enumerate(contents, start=1):
            chapter = Chapter(
                novel_id=novel.id,
                chapter_number=i,
                title=f"C{i}",
                content=content,
                word_count=len(content),
            )
            session.add(chapter)
            session.flush()
            chapter_ids.append(chapter.id)

        records = [
            ChapterRecord(
                chapter_id=chapter_id,
                chapter_number=i + 1,
                content=content,
            )
            for i, (chapter_id, content) in enumerate(zip(chapter_ids, contents))
        ]
        snapshot_hash = compute_source_snapshot_hash(
            owner_id=user.id, novel_id=novel.id, chapters=records
        )
        scene_ids: list[str] = []
        coordinates: dict[str, dict[str, Any]] = {}
        for i, (chapter_id, content) in enumerate(
            zip(chapter_ids, contents), start=1
        ):
            outcome = detect_chapter_boundaries(
                novel_id=novel.id,
                chapter_id=chapter_id,
                chapter_number=i,
                content=content,
                source_snapshot_hash=snapshot_hash,
            )
            for boundary in outcome.boundaries:
                scene_ids.append(boundary.scene_id)
                coordinates[boundary.scene_id] = _scene_coordinates(i)
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "chapter_ids": chapter_ids,
            "contents": list(contents),
            "snapshot_hash": snapshot_hash,
            "scene_ids": scene_ids,
            "coordinates": coordinates,
            "token": create_access_token({"sub": str(user.id)}),
        }
    engine.dispose()
    return data


def _generate_payload(
    ids: dict[str, Any],
    *,
    version_key: str = "ks-main",
    cutoff: int = 3,
    max_candidates: int | None = None,
    source_snapshot_id: str = "ss-1",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version_key": version_key,
        "cutoff_chapter": cutoff,
        "source_snapshot_id": source_snapshot_id,
        "coordinates": ids["coordinates"],
    }
    if max_candidates is not None:
        payload["max_candidates"] = max_candidates
    return payload


# ---------------------------------------------------------------------------
# Owner scope / IDOR matrix
# ---------------------------------------------------------------------------


async def test_cross_owner_key_scene_matrix_404(api_client):
    client, _, sync_url = api_client
    ids_a = _seed_owner(sync_url, suffix=f"a_{uuid.uuid4().hex[:8]}")
    ids_b = _seed_owner(sync_url, suffix=f"b_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {ids_a['token']}"}
    headers_b = {"Authorization": f"Bearer {ids_b['token']}"}

    base_a = f"/api/novels/{ids_a['novel_id']}/key-scenes"
    created = await client.post(
        f"{base_a}/generate",
        json=_generate_payload(ids_a),
        headers=headers_a,
    )
    assert created.status_code == 201, created.text
    set_id = created.json()["set"]["id"]

    # Owner B probing owner A's novel: every route is an identical 404.
    foreign_list = await client.get(base_a, headers=headers_b)
    assert foreign_list.status_code == 404
    foreign_detail = await client.get(f"{base_a}/{set_id}", headers=headers_b)
    assert foreign_detail.status_code == 404
    foreign_generate = await client.post(
        f"{base_a}/generate", json=_generate_payload(ids_a), headers=headers_b
    )
    assert foreign_generate.status_code == 404

    missing_novel = await client.get(
        "/api/novels/999999991/key-scenes", headers=headers_b
    )
    assert missing_novel.status_code == 404
    assert foreign_list.json() == missing_novel.json()

    # B sees an empty list for B's own novel; A still reads A's set.
    ok_b = await client.get(
        f"/api/novels/{ids_b['novel_id']}/key-scenes", headers=headers_b
    )
    assert ok_b.status_code == 200
    assert ok_b.json()["total"] == 0
    ok_a = await client.get(base_a, headers=headers_a)
    assert ok_a.json()["total"] == 1


async def test_unauthenticated_key_scene_routes_reject(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"anon_{uuid.uuid4().hex[:8]}")
    base = f"/api/novels/{ids['novel_id']}/key-scenes"
    resp = await client.get(base)
    assert resp.status_code == 401
    resp2 = await client.post(f"{base}/generate", json=_generate_payload(ids))
    assert resp2.status_code == 401


# ---------------------------------------------------------------------------
# Generation exposes evidence + reasons + diversity + heuristic metadata
# ---------------------------------------------------------------------------


async def test_generate_exposes_evidence_reasons_diversity_and_heuristic(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cand_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/key-scenes"

    resp = await client.post(
        f"{base}/generate", json=_generate_payload(ids), headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replayed"] is False
    set_view = body["set"]
    assert set_view["owner_id"] == ids["owner_id"]
    assert set_view["novel_id"] == ids["novel_id"]
    assert set_view["cutoff_chapter"] == 3
    assert set_view["review_state"] == "candidate"
    assert set_view["source_snapshot_hash"] == ids["snapshot_hash"]
    assert len(set_view["manifest_hash"]) == 64

    candidates = set_view["candidates"]
    assert len(candidates) == len(ids["scene_ids"])
    chapter_numbers = {c["chapter_number"] for c in candidates}
    assert chapter_numbers == {1, 2, 3}

    # Ordered candidates carry evidence refs, reasons and diversity keys.
    for candidate in candidates:
        assert len(candidate["source_hash"]) == 64
        assert candidate["diversity_key"]
        assert candidate["score_total"] >= 0.0
        assert candidate["policy_hash"]
        assert candidate["evidence_ranges"], "evidence is the citation authority"
        ref = candidate["evidence_ranges"][0]
        assert ref["source_snapshot_hash"] == ids["snapshot_hash"]
        assert ref["cutoff_chapter"] == 3
        assert len(ref["content_hash"]) == 64
        assert ref["source_end"] > ref["source_start"]
        assert candidate["chapter_number"] <= 3

    # Diversity keys: one distinct scene per chapter coordinates.
    assert len({c["diversity_key"] for c in candidates}) == len(candidates)


async def test_action_candidate_exposes_available_heuristic_signal(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"heu_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/key-scenes"

    resp = await client.post(
        f"{base}/generate", json=_generate_payload(ids), headers=headers
    )
    assert resp.status_code == 201, resp.text
    candidates = resp.json()["set"]["candidates"]
    action = next(
        c for c in candidates if c["chapter_number"] == 1
    )
    signal = action["heuristic_signal"]
    assert signal is not None
    assert signal["availability"] == "available"
    assert signal["confidence"] == 0.9
    assert signal["speaker_offsets"]
    assert signal["dialogue_offsets"]
    assert signal["warnings"] == []

    # The advisory dialogue contribution only changes ranking; it is never a
    # citation. Evidence ranges carry only source-verified fields.
    assert any(
        r["reason_code"] == "dialogue_turn"
        for r in action["salience_reasons"]
    )
    ref_keys = set(action["evidence_ranges"][0])
    assert ref_keys >= {
        "evidence_key",
        "source_snapshot_id",
        "source_snapshot_hash",
        "chapter_id",
        "chapter_number",
        "source_start",
        "source_end",
        "content_hash",
        "cutoff_chapter",
    }
    assert "speaker_offsets" not in ref_keys
    assert "confidence" not in ref_keys
    assert "warnings" not in ref_keys

    # Heuristic offsets stay inside the candidate's own primary evidence range.
    primary = action["evidence_ranges"][0]
    for offset in [*signal["speaker_offsets"], *signal["dialogue_offsets"]]:
        assert primary["source_start"] <= offset["offset_start"]
        assert offset["offset_end"] <= primary["source_end"]


async def test_quiet_and_ambiguous_candidates_preserve_signals(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"sig_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/key-scenes"

    resp = await client.post(
        f"{base}/generate", json=_generate_payload(ids), headers=headers
    )
    assert resp.status_code == 201, resp.text
    by_chapter = {
        c["chapter_number"]: c for c in resp.json()["set"]["candidates"]
    }
    # Quiet chapter: no dialogue → explicit unavailable, never silent zero.
    quiet = by_chapter[2]
    quiet_signal = quiet["heuristic_signal"]
    assert quiet_signal["availability"] == "unavailable"
    assert quiet_signal["confidence"] is None
    assert quiet_signal["speaker_offsets"] == []
    assert "no_dialogue_detected" in quiet_signal["warnings"]
    assert quiet["score_breakdown"]["dialogue_turn"] == 0.0
    # Quiet-emotional salience survives as a reason code.
    assert any(
        r["reason_code"] == "quiet_emotional"
        for r in quiet["salience_reasons"]
    )

    # Ambiguous chapter: visually weak, no strong signal → still a candidate.
    ambiguous = by_chapter[3]
    assert ambiguous["review_state"] == "candidate"
    assert ambiguous["score_total"] >= 0.0


# ---------------------------------------------------------------------------
# Spoiler cutoff (server-side)
# ---------------------------------------------------------------------------


async def test_spoiler_cutoff_excludes_future_chapter_candidates(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"spoiler_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/key-scenes"

    resp = await client.post(
        f"{base}/generate",
        json=_generate_payload(ids, cutoff=1),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    set_view = resp.json()["set"]
    assert set_view["cutoff_chapter"] == 1
    assert {c["chapter_number"] for c in set_view["candidates"]} == {1}
    assert all(c["chapter_number"] <= set_view["cutoff_chapter"] for c in set_view["candidates"])
    assert all(c["spoiler_cutoff"] == 1 for c in set_view["candidates"])
    # Each seeded chapter is a single scene; cutoff=1 keeps only chapter 1.
    assert len(set_view["candidates"]) == 1


# ---------------------------------------------------------------------------
# Candidate-only / no canon promotion / source immutability
# ---------------------------------------------------------------------------


async def test_generate_never_promotes_candidate_or_touches_source(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"immut_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/key-scenes"

    resp = await client.post(
        f"{base}/generate", json=_generate_payload(ids), headers=headers
    )
    assert resp.status_code == 201, resp.text
    set_view = resp.json()["set"]
    # Candidate-only: no canon/promote/pointer field exists on the envelope.
    assert "canon" not in set_view
    assert "promote_to_canon" not in set_view
    assert "cover_url" not in set_view
    assert set_view["review_state"] == "candidate"
    assert all(
        c["review_state"] == "candidate" for c in set_view["candidates"]
    )

    # The authoritative chapter body is untouched by generation.
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        stored = session.get(Chapter, ids["chapter_ids"][0])
        assert stored.content == ids["contents"][0]
    engine.dispose()


# ---------------------------------------------------------------------------
# Idempotent replay + conflicting retry
# ---------------------------------------------------------------------------


async def test_identical_generate_replays_same_set(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"replay_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/key-scenes"
    payload = _generate_payload(ids)

    first = await client.post(f"{base}/generate", json=payload, headers=headers)
    assert first.status_code == 201
    second = await client.post(f"{base}/generate", json=payload, headers=headers)
    assert second.status_code == 201
    assert second.json()["replayed"] is True
    assert second.json()["set"]["id"] == first.json()["set"]["id"]
    assert second.json()["set"]["manifest_hash"] == first.json()["set"]["manifest_hash"]

    listing = await client.get(base, headers=headers)
    assert listing.json()["total"] == 1


async def test_conflicting_generate_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"conflict_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/key-scenes"

    assert (
        await client.post(
            f"{base}/generate", json=_generate_payload(ids), headers=headers
        )
    ).status_code == 201

    # Same version_key but a different immutable cutoff → conflict, fail closed.
    conflicting = await client.post(
        f"{base}/generate",
        json=_generate_payload(ids, cutoff=2),
        headers=headers,
    )
    assert conflicting.status_code == 409, conflicting.text
    assert "version_key" in conflicting.json()["detail"]


async def test_max_candidates_budget_gate_bounds_the_set(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"budget_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/key-scenes"

    resp = await client.post(
        f"{base}/generate",
        json=_generate_payload(ids, max_candidates=2),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["set"]["candidates"]) == 2


# ---------------------------------------------------------------------------
# Service-level seam: persistence, replay and owner scope
# ---------------------------------------------------------------------------


async def test_service_generates_persists_and_owner_scope_fails_closed(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"svc_{uuid.uuid4().hex[:8]}")
    request = CandidateGenerationInput(
        version_key="ks-svc",
        cutoff_chapter=3,
        source_snapshot_id="ss-1",
        coordinates={
            scene_id: SceneCoordinates.model_validate(coordinates)
            for scene_id, coordinates in ids["coordinates"].items()
        },
    )

    async with factory() as session:
        service = CandidateService(session)
        persisted = await service.generate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            input_=request,
        )
        assert persisted.replayed is False
        set_id = persisted.set.id

        # Content rows were persisted append-only.
        assert (
            await session.scalar(
                select(SceneCandidate).where(
                    SceneCandidate.set_id == set_id
                )
            )
            is not None
        )
        assert (
            await session.scalar(
                select(SceneEvidenceRange).where(
                    SceneEvidenceRange.set_id == set_id
                )
            )
            is not None
        )

        # Cross-owner read of the same set_id fails closed.
        assert (
            await session.scalar(
                select(SceneCandidateSet).where(
                    SceneCandidateSet.owner_id == ids["owner_id"] + 1,
                    SceneCandidateSet.novel_id == ids["novel_id"],
                    SceneCandidateSet.id == set_id,
                )
            )
            is None
        )
        await session.rollback()
