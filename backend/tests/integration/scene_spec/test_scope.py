"""SceneSpec compiler integration tests (Phase 32-02, REQ-VIS-03).

Covers the 32-VALIDATION.md integration matrix:
- owner/novel/snapshot/Visual Bible revision/candidate-only gates all hold at
  the API boundary; cross-owner access fails closed (no owner leak);
- preview compiles without persisting anything and without any provider call;
- create persists an immutable candidate; identical create replays, a
  conflicting spec_key retry fails closed;
- after the Visual Bible revision changes, the stored spec is marked stale and
  the diff endpoint shows exactly which canonical sections drifted;
- every positive/negative clause carries a provenance that traces back to
  evidence or the Visual Bible revision.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.security import create_access_token, hash_password
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.schemas.visual_bible import (
    VisualBibleVersionContract,
    VisualClaimContract,
    claim_content_hash,
    recompute_manifest_hash,
)
from app.services.key_scenes.boundaries import (
    ChapterRecord,
    compute_source_snapshot_hash,
    detect_chapter_boundaries,
)
from app.services.visual_bible.evidence import (
    ChapterRecord as VisualBibleChapterRecord,
    compute_source_snapshot_hash as compute_visual_bible_snapshot_hash,
)

pytestmark = pytest.mark.integration

HEX64 = "a" * 64

CH1 = "Ayla was a tall young woman with braided amber hair and green eyes. She wore a grey wool cloak and drew her sword."
CH2 = "The stone hall of the northern keep stood cold; its tall windows let in pale light."
CH3 = "Mara watched the courtyard in the rain and tightened the string of her bow."


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_owner(
    sync_url: str, *, suffix: str, contents: tuple[str, ...] | None = None
) -> dict[str, Any]:
    contents = contents or (CH1, CH2, CH3)
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"ss_{suffix}",
            email=f"ss_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"SS Novel {suffix}",
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
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "chapter_ids": chapter_ids,
            "contents": list(contents),
            "token": create_access_token({"sub": str(user.id)}),
        }
    engine.dispose()
    return data


def _snapshot_hash(ids: dict[str, Any]) -> str:
    """Key-scene domain snapshot hash (the set's evidence lineage)."""
    chapters = [
        ChapterRecord(
            chapter_id=chapter_id,
            chapter_number=i + 1,
            content=content,
        )
        for i, (chapter_id, content) in enumerate(
            zip(ids["chapter_ids"], ids["contents"])
        )
    ]
    return compute_source_snapshot_hash(
        owner_id=ids["owner_id"], novel_id=ids["novel_id"], chapters=chapters
    )


def _visual_bible_snapshot_hash(ids: dict[str, Any]) -> str:
    """Visual Bible domain snapshot hash (its own lineage domain)."""
    chapters = [
        VisualBibleChapterRecord(
            chapter_id=chapter_id,
            chapter_number=i + 1,
            content=content,
        )
        for i, (chapter_id, content) in enumerate(
            zip(ids["chapter_ids"], ids["contents"])
        )
    ]
    return compute_visual_bible_snapshot_hash(
        owner_id=ids["owner_id"], novel_id=ids["novel_id"], chapters=chapters
    )


def _scene_coordinates(chapter_number: int) -> dict[str, Any]:
    if chapter_number == 1:
        return {
            "cast": ["ayla"],
            "place": "northern keep",
            "time": "night",
            "pov": "ayla",
        }
    if chapter_number == 2:
        return {"cast": ["ayla"], "place": "hall", "time": "day", "pov": "ayla"}
    return {"cast": ["mara"], "place": "courtyard", "time": "night", "pov": "mara"}


def _evidence_payload(
    *,
    content: str,
    find_text: str,
    evidence_key: str,
    chapter_id: int,
    chapter_number: int,
    source_snapshot_id: str,
    source_snapshot_hash: str,
    cutoff_chapter: int,
) -> dict[str, Any]:
    start = content.find(find_text)
    assert start >= 0, f"{find_text!r} not found in chapter"
    end = start + len(find_text)
    return {
        "evidence_key": evidence_key,
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_hash": source_snapshot_hash,
        "chapter_id": chapter_id,
        "chapter_number": chapter_number,
        "source_start": start,
        "source_end": end,
        "content_hash": _sha256(content[start:end]),
        "excerpt": content[start:end],
        "cutoff_chapter": cutoff_chapter,
    }


def build_version_payload(
    ids: dict[str, Any],
    *,
    snapshot_hash: str,
    snapshot_id: str,
    cutoff_chapter: int,
    version_key: str = "vb-main",
) -> dict[str, Any]:
    """One approved-candidate Visual Bible version payload with canon claims."""
    ayla_evidence = _evidence_payload(
        content=ids["contents"][0],
        find_text="braided amber hair and green eyes",
        evidence_key="ev-ayla-hair",
        chapter_id=ids["chapter_ids"][0],
        chapter_number=1,
        source_snapshot_id=snapshot_id,
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=cutoff_chapter,
    )
    mara_evidence = _evidence_payload(
        content=ids["contents"][2],
        find_text="tightened the string of her bow",
        evidence_key="ev-mara-bow",
        chapter_id=ids["chapter_ids"][2],
        chapter_number=3,
        source_snapshot_id=snapshot_id,
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=cutoff_chapter,
    )
    entities = [
        {
            "stable_id": "ayla",
            "entity_key": "ayla",
            "entity_type": "character",
            "description": "Ayla, a tall woman with braided amber hair and green eyes",
            "authority": "canon_fact",
            "disclosure_cutoff": cutoff_chapter,
        },
        {
            "stable_id": "mara",
            "entity_key": "mara",
            "entity_type": "character",
            "description": "Mara, a watchful archer who keeps her bow ready",
            "authority": "canon_fact",
            "disclosure_cutoff": cutoff_chapter,
        },
        {
            "stable_id": "northern keep",
            "entity_key": "northern keep",
            "entity_type": "place",
            "description": "The stone hall of the northern keep with tall cold windows",
            "authority": "canon_fact",
            "disclosure_cutoff": cutoff_chapter,
        },
    ]
    ayla_claim = VisualClaimContract.model_validate(
        {
            "claim_key": "c-ayla-appearance",
            "entity_stable_id": "ayla",
            "authority": "canon_fact",
            "description": "Ayla has braided amber hair and green eyes",
            "author": None,
            "rationale": None,
            "cutoff_chapter": cutoff_chapter,
            "claim_hash": "0" * 64,
            "evidence_refs": [ayla_evidence],
        }
    )
    ayla_claim = ayla_claim.model_copy(
        update={"claim_hash": claim_content_hash(ayla_claim)}
    )
    mara_claim = VisualClaimContract.model_validate(
        {
            "claim_key": "c-mara-bow",
            "entity_stable_id": "mara",
            "authority": "canon_fact",
            "description": "Mara is an archer who tightens her bowstring",
            "author": None,
            "rationale": None,
            "cutoff_chapter": cutoff_chapter,
            "claim_hash": "0" * 64,
            "evidence_refs": [mara_evidence],
        }
    )
    mara_claim = mara_claim.model_copy(
        update={"claim_hash": claim_content_hash(mara_claim)}
    )
    payload = {
        "schema_version": "visual-bible.v1",
        "artifact_kind": "visual_bible",
        "owner_id": ids["owner_id"],
        "novel_id": ids["novel_id"],
        "version_key": version_key,
        "revision_number": 1,
        "parent_version_id": None,
        "source_snapshot_id": snapshot_id,
        "source_snapshot_hash": snapshot_hash,
        "cutoff_chapter": cutoff_chapter,
        "schema_hash": HEX64,
        "policy_hash": HEX64,
        "prompt_hash": HEX64,
        "model_hash": None,
        "config_hash": None,
        "manifest_hash": "0" * 64,
        "style_profile": {"palette": "muted cold tones", "lighting": "overcast"},
        "constraints": [
            {
                "constraint_key": "nc-no-modern-era",
                "scope": "era",
                "source": "visual_bible",
                "text": "the scene must stay in the medieval era; no modern objects",
            },
            {
                "constraint_key": "nc-no-ornate-armor",
                "scope": "costume",
                "source": "visual_bible",
                "text": "do not add ornate armor to Ayla",
            },
        ],
        "entities": entities,
        "claims": [
            ayla_claim.model_dump(mode="json"),
            mara_claim.model_dump(mode="json"),
        ],
        "reference_assets": [],
        "review_state": "candidate",
    }
    version = VisualBibleVersionContract.model_validate(payload)
    version = version.model_copy(
        update={"manifest_hash": recompute_manifest_hash(version)}
    )
    return {"version": version.model_dump(mode="json")}


def _generate_payload(
    ids: dict[str, Any],
    *,
    coordinates: dict[str, dict[str, Any]],
    scene_ids: list[str],
    snapshot_hash: str,
    vb_version_id: int,
    vb_manifest_hash: str,
    cutoff: int = 3,
) -> dict[str, Any]:
    return {
        "version_key": f"ks-{uuid.uuid4().hex[:8]}",
        "cutoff_chapter": cutoff,
        "source_snapshot_id": "ss-main",
        "coordinates": coordinates,
        "approved_visual_bible_revision_id": vb_version_id,
        "approved_visual_bible_revision_hash": vb_manifest_hash,
    }


def _scene_ids(
    ids: dict[str, Any], snapshot_hash: str
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    scene_ids: list[str] = []
    coordinates: dict[str, dict[str, Any]] = {}
    for i, (chapter_id, content) in enumerate(
        zip(ids["chapter_ids"], ids["contents"]), start=1
    ):
        outcome = detect_chapter_boundaries(
            novel_id=ids["novel_id"],
            chapter_id=chapter_id,
            chapter_number=i,
            content=content,
            source_snapshot_hash=snapshot_hash,
        )
        for boundary in outcome.boundaries:
            scene_ids.append(boundary.scene_id)
            coordinates[boundary.scene_id] = _scene_coordinates(i)
    return scene_ids, coordinates


async def _create_and_approve_visual_bible(
    client: Any,
    ids: dict[str, Any],
    headers: dict[str, str],
    *,
    snapshot_hash: str,
    version_key: str = "vb-main",
) -> dict[str, Any]:
    payload = build_version_payload(
        ids,
        snapshot_hash=snapshot_hash,
        snapshot_id="ss-main",
        cutoff_chapter=3,
        version_key=version_key,
    )
    base = f"/api/novels/{ids['novel_id']}/visual-bible"
    created = await client.post(base, json=payload, headers=headers)
    assert created.status_code == 201, created.text
    version_id = created.json()["version"]["id"]
    manifest_hash = created.json()["version"]["manifest_hash"]
    approved = await client.post(
        f"{base}/{version_id}/review",
        json={
            "action": "approve",
            "actor_source": "human",
            "actor": "test-reviewer",
            "reason": "approved for key-scene freeze",
            "event_key": f"approve-{version_key}-{uuid.uuid4().hex[:8]}",
            "from_review_state": "candidate",
        },
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_state"] == "approved"
    return {"id": version_id, "manifest_hash": manifest_hash}


async def _freeze_key_scene_set(
    client: Any,
    ids: dict[str, Any],
    headers: dict[str, str],
    *,
    snapshot_hash: str,
    vb_version_id: int,
    vb_manifest_hash: str,
) -> dict[str, Any]:
    scene_ids, coordinates = _scene_ids(ids, snapshot_hash)
    base = f"/api/novels/{ids['novel_id']}/key-scenes"
    payload = _generate_payload(
        ids,
        coordinates=coordinates,
        scene_ids=scene_ids,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb_version_id,
        vb_manifest_hash=vb_manifest_hash,
    )
    generated = await client.post(f"{base}/generate", json=payload, headers=headers)
    assert generated.status_code == 201, generated.text
    set_view = generated.json()["set"]
    set_id = set_view["id"]

    for candidate in set_view["candidates"]:
        review = await client.post(
            f"{base}/{set_id}/review",
            json={
                "decision_key": f"approve-{candidate['candidate_key']}-{uuid.uuid4().hex[:8]}",
                "action": "approve",
                "actor_source": "human",
                "actor": "test-reviewer",
                "reason": "approved candidate for freeze",
                "from_review_state": "candidate",
                "candidate_key": candidate["candidate_key"],
            },
            headers=headers,
        )
        assert review.status_code == 200, review.text

    frozen = await client.post(
        f"{base}/{set_id}/freeze",
        json={
            "actor_source": "human",
            "actor": "test-reviewer",
            "reason": "freeze set for scene-spec compile",
        },
        headers=headers,
    )
    assert frozen.status_code == 200, frozen.text
    return {
        "set_id": set_id,
        "candidate_key": set_view["candidates"][0]["candidate_key"],
        "candidate_keys": [c["candidate_key"] for c in set_view["candidates"]],
        "snapshot_hash": set_view["source_snapshot_hash"],
        "coordinates": coordinates,
        "scene_ids": scene_ids,
    }


def _spec_preview_payload(
    frozen: dict[str, Any],
    *,
    spec_key: str,
    vb_version_id: int,
) -> dict[str, Any]:
    return {
        "spec_key": spec_key,
        "candidate_set_id": frozen["set_id"],
        "candidate_key": frozen["candidate_key"],
        "visual_bible_version_id": vb_version_id,
        "source_snapshot_id": "ss-main",
        "revision_number": 1,
    }


# ---------------------------------------------------------------------------
# Owner scope / IDOR matrix
# ---------------------------------------------------------------------------


async def test_cross_owner_scene_spec_matrix_404(api_client):
    client, _, sync_url = api_client
    ids_a = _seed_owner(sync_url, suffix=f"a_{uuid.uuid4().hex[:8]}")
    ids_b = _seed_owner(sync_url, suffix=f"b_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {ids_a['token']}"}
    headers_b = {"Authorization": f"Bearer {ids_b['token']}"}
    snapshot_hash = _snapshot_hash(ids_a)
    vb_snapshot_hash = _visual_bible_snapshot_hash(ids_a)

    vb_a = await _create_and_approve_visual_bible(
        client, ids_a, headers_a, snapshot_hash=vb_snapshot_hash
    )
    frozen_a = await _freeze_key_scene_set(
        client,
        ids_a,
        headers_a,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb_a["id"],
        vb_manifest_hash=vb_a["manifest_hash"],
    )
    base_a = f"/api/novels/{ids_a['novel_id']}/scene-specs"
    created = await client.post(
        base_a,
        json=_spec_preview_payload(
            frozen_a, spec_key="spec-a", vb_version_id=vb_a["id"]
        ),
        headers=headers_a,
    )
    assert created.status_code == 201, created.text
    spec_id = created.json()["spec"]["id"]

    # Owner B probing owner A's novel: every route is an identical 404.
    foreign_list = await client.get(base_a, headers=headers_b)
    assert foreign_list.status_code == 404
    foreign_detail = await client.get(f"{base_a}/{spec_id}", headers=headers_b)
    assert foreign_detail.status_code == 404
    foreign_diff = await client.get(f"{base_a}/{spec_id}/diff", headers=headers_b)
    assert foreign_diff.status_code == 404
    foreign_preview = await client.post(
        f"{base_a}/preview",
        json=_spec_preview_payload(
            frozen_a, spec_key="spec-x", vb_version_id=vb_a["id"]
        ),
        headers=headers_b,
    )
    assert foreign_preview.status_code == 404

    missing_novel = await client.get(
        "/api/novels/999999991/scene-specs", headers=headers_b
    )
    assert missing_novel.status_code == 404
    assert foreign_list.json() == missing_novel.json()


async def test_unauthenticated_scene_spec_routes_reject(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"anon_{uuid.uuid4().hex[:8]}")
    base = f"/api/novels/{ids['novel_id']}/scene-specs"
    assert (await client.get(base)).status_code == 401
    assert (await client.post(f"{base}/preview", json={})).status_code == 401


# ---------------------------------------------------------------------------
# Preview: no persistence, no provider call
# ---------------------------------------------------------------------------


async def test_preview_compiles_without_persisting_and_without_provider(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"pv_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    snapshot_hash = _snapshot_hash(ids)
    vb_snapshot_hash = _visual_bible_snapshot_hash(ids)
    vb = await _create_and_approve_visual_bible(
        client, ids, headers, snapshot_hash=vb_snapshot_hash
    )
    frozen = await _freeze_key_scene_set(
        client,
        ids,
        headers,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb["id"],
        vb_manifest_hash=vb["manifest_hash"],
    )
    base = f"/api/novels/{ids['novel_id']}/scene-specs"

    preview = await client.post(
        f"{base}/preview",
        json=_spec_preview_payload(frozen, spec_key="spec-pv", vb_version_id=vb["id"]),
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["persisted"] is False
    assert body["provider_calls"] == 0, "preview must never call a provider"
    spec = body["spec"]
    assert spec["owner_id"] == ids["owner_id"]
    assert spec["novel_id"] == ids["novel_id"]
    assert spec["cutoff_chapter"] == 3
    assert spec["review_state"] == "candidate"
    assert len(spec["content_hash"]) == 64
    assert spec["visual_bible_revision_hash"] == vb["manifest_hash"]
    assert spec["scene_candidate_hash"]

    # Nothing was persisted by preview.
    listing = await client.get(base, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 0

    # Every positive and negative clause carries provenance.
    assert spec["details"], "spec must carry deterministic details"
    for detail in spec["details"]:
        if detail["source"] == "evidence":
            assert detail["evidence_keys"], "evidence clause must cite evidence"
        if detail["source"] == "visual_bible":
            assert detail["visual_bible_stable_ids"]
    assert spec["negative_constraints"], "negative constraints must be preserved"
    for constraint in spec["negative_constraints"]:
        assert constraint["scope"] in {
            "costume",
            "era",
            "identity",
            "style",
            "physical",
            "continuity",
        }
    # Continuity clause keeps stable Visual Bible IDs.
    continuity = next(d for d in spec["details"] if d["kind"] == "continuity")
    assert "ayla" in continuity["visual_bible_stable_ids"]


# ---------------------------------------------------------------------------
# Create: append-only persistence + idempotent replay + conflict
# ---------------------------------------------------------------------------


async def test_create_persists_candidate_and_replays_identically(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cr_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    snapshot_hash = _snapshot_hash(ids)
    vb_snapshot_hash = _visual_bible_snapshot_hash(ids)
    vb = await _create_and_approve_visual_bible(
        client, ids, headers, snapshot_hash=vb_snapshot_hash
    )
    frozen = await _freeze_key_scene_set(
        client,
        ids,
        headers,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb["id"],
        vb_manifest_hash=vb["manifest_hash"],
    )
    base = f"/api/novels/{ids['novel_id']}/scene-specs"
    payload = _spec_preview_payload(frozen, spec_key="spec-cr", vb_version_id=vb["id"])

    first = await client.post(base, json=payload, headers=headers)
    assert first.status_code == 201, first.text
    assert first.json()["replayed"] is False
    spec_id = first.json()["spec"]["id"]

    second = await client.post(base, json=payload, headers=headers)
    assert second.status_code == 201, second.text
    assert second.json()["replayed"] is True
    assert second.json()["spec"]["id"] == spec_id
    assert second.json()["spec"]["content_hash"] == first.json()["spec"]["content_hash"]

    listing = await client.get(base, headers=headers)
    assert listing.json()["total"] == 1


async def test_conflicting_create_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cf_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    snapshot_hash = _snapshot_hash(ids)
    vb_snapshot_hash = _visual_bible_snapshot_hash(ids)
    vb = await _create_and_approve_visual_bible(
        client, ids, headers, snapshot_hash=vb_snapshot_hash
    )
    frozen = await _freeze_key_scene_set(
        client,
        ids,
        headers,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb["id"],
        vb_manifest_hash=vb["manifest_hash"],
    )
    base = f"/api/novels/{ids['novel_id']}/scene-specs"
    assert (
        await client.post(
            base,
            json=_spec_preview_payload(
                frozen, spec_key="spec-cf", vb_version_id=vb["id"]
            ),
            headers=headers,
        )
    ).status_code == 201

    # Same spec_key with a different approved candidate → conflicting immutable
    # content; the retry must fail closed (no second row, no overwrite).
    if len(frozen["candidate_keys"]) > 1:
        conflicting_payload = _spec_preview_payload(
            frozen, spec_key="spec-cf", vb_version_id=vb["id"]
        )
        conflicting_payload["candidate_key"] = frozen["candidate_keys"][1]
        conflicting = await client.post(base, json=conflicting_payload, headers=headers)
        assert conflicting.status_code == 409, conflicting.text
        assert "spec_key" in conflicting.json()["detail"]


# ---------------------------------------------------------------------------
# Staleness: Visual Bible revision change marks the spec stale + diff
# ---------------------------------------------------------------------------


async def test_visual_bible_change_marks_spec_stale_and_shows_diff(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"st_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    snapshot_hash = _snapshot_hash(ids)
    vb_snapshot_hash = _visual_bible_snapshot_hash(ids)
    vb = await _create_and_approve_visual_bible(
        client, ids, headers, snapshot_hash=vb_snapshot_hash
    )
    frozen = await _freeze_key_scene_set(
        client,
        ids,
        headers,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb["id"],
        vb_manifest_hash=vb["manifest_hash"],
    )
    base = f"/api/novels/{ids['novel_id']}/scene-specs"
    created = await client.post(
        base,
        json=_spec_preview_payload(frozen, spec_key="spec-st", vb_version_id=vb["id"]),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    spec_id = created.json()["spec"]["id"]

    fresh = await client.get(f"{base}/{spec_id}", headers=headers)
    assert fresh.status_code == 200
    assert fresh.json()["stale"] is False

    # Approve a NEW Visual Bible revision (different version_key + style).
    vb2_payload = build_version_payload(
        ids,
        snapshot_hash=vb_snapshot_hash,
        snapshot_id="ss-main",
        cutoff_chapter=3,
        version_key="vb-v2",
    )
    vb2_payload["version"]["style_profile"] = {
        "palette": "warm tones",
        "lighting": "golden hour",
    }
    vb2_version = VisualBibleVersionContract.model_validate(vb2_payload["version"])
    vb2_version = vb2_version.model_copy(
        update={"manifest_hash": recompute_manifest_hash(vb2_version)}
    )
    vb2_payload["version"] = vb2_version.model_dump(mode="json")
    vb2_created = await client.post(
        f"/api/novels/{ids['novel_id']}/visual-bible",
        json=vb2_payload,
        headers=headers,
    )
    assert vb2_created.status_code == 201, vb2_created.text
    vb2_id = vb2_created.json()["version"]["id"]
    approved_v2 = await client.post(
        f"/api/novels/{ids['novel_id']}/visual-bible/{vb2_id}/review",
        json={
            "action": "approve",
            "actor_source": "human",
            "actor": "test-reviewer",
            "reason": "approve v2",
            "event_key": f"approve-v2-{uuid.uuid4().hex[:8]}",
            "from_review_state": "candidate",
        },
        headers=headers,
    )
    assert approved_v2.status_code == 200, approved_v2.text

    # The stored spec was compiled against the old revision → now stale.
    stale = await client.get(f"{base}/{spec_id}", headers=headers)
    assert stale.status_code == 200
    assert stale.json()["stale"] is True

    diff = await client.get(f"{base}/{spec_id}/diff", headers=headers)
    assert diff.status_code == 200, diff.text
    body = diff.json()
    assert body["stale"] is True
    assert body["same"] is False
    assert body["original_spec_hash"] == created.json()["spec"]["content_hash"]
    assert len(body["changed_sections"]) >= 1
    # The style section drifted because the new revision changed the profile.
    changed_keys = {section["section_key"] for section in body["changed_sections"]}
    assert "style" in changed_keys


# ---------------------------------------------------------------------------
# Candidate-only: no canon promotion, no source rewrite
# ---------------------------------------------------------------------------


async def test_scene_spec_never_promotes_canon_or_rewrites_source(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cand_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    snapshot_hash = _snapshot_hash(ids)
    vb_snapshot_hash = _visual_bible_snapshot_hash(ids)
    vb = await _create_and_approve_visual_bible(
        client, ids, headers, snapshot_hash=vb_snapshot_hash
    )
    frozen = await _freeze_key_scene_set(
        client,
        ids,
        headers,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb["id"],
        vb_manifest_hash=vb["manifest_hash"],
    )
    base = f"/api/novels/{ids['novel_id']}/scene-specs"
    created = await client.post(
        base,
        json=_spec_preview_payload(frozen, spec_key="spec-c", vb_version_id=vb["id"]),
        headers=headers,
    )
    assert created.status_code == 201
    spec = created.json()["spec"]
    assert spec["review_state"] == "candidate"
    assert "canon" not in spec
    assert "promote_to_canon" not in spec
    assert "cover_url" not in spec

    # Authoritative chapter body is untouched.
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        stored = session.get(Chapter, ids["chapter_ids"][0])
        assert stored.content == ids["contents"][0]
    engine.dispose()


async def test_service_persists_content_rows(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"svc_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    snapshot_hash = _snapshot_hash(ids)
    vb_snapshot_hash = _visual_bible_snapshot_hash(ids)
    vb = await _create_and_approve_visual_bible(
        client, ids, headers, snapshot_hash=vb_snapshot_hash
    )
    frozen = await _freeze_key_scene_set(
        client,
        ids,
        headers,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb["id"],
        vb_manifest_hash=vb["manifest_hash"],
    )
    base = f"/api/novels/{ids['novel_id']}/scene-specs"
    created = await client.post(
        base,
        json=_spec_preview_payload(frozen, spec_key="spec-svc", vb_version_id=vb["id"]),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    spec_id = created.json()["spec"]["id"]

    from app.models.scene_spec import (
        SceneSpecDetail,
        SceneSpecEvidenceRef,
        SceneSpecNegativeConstraint,
        SceneSpecVersion,
    )

    async with factory() as session:
        version = await session.scalar(
            select(SceneSpecVersion).where(
                SceneSpecVersion.owner_id == ids["owner_id"],
                SceneSpecVersion.novel_id == ids["novel_id"],
                SceneSpecVersion.id == spec_id,
            )
        )
        assert version is not None
        assert version.review_state == "candidate"
        details = (
            await session.scalars(
                select(SceneSpecDetail).where(SceneSpecDetail.spec_id == spec_id)
            )
        ).all()
        assert details, "detail rows must be persisted"
        constraints = (
            await session.scalars(
                select(SceneSpecNegativeConstraint).where(
                    SceneSpecNegativeConstraint.spec_id == spec_id
                )
            )
        ).all()
        assert constraints, "negative constraint rows must be persisted"
        evidence = (
            await session.scalars(
                select(SceneSpecEvidenceRef).where(
                    SceneSpecEvidenceRef.spec_id == spec_id
                )
            )
        ).all()
        assert evidence, "evidence ref rows must be persisted"
        assert all(
            (row.detail_id is None) != (row.constraint_id is None) for row in evidence
        )
        # Content rows are immutable append-only records; no in-place canon
        # promotion or evidence mutation is possible for this spec.
        await session.rollback()
