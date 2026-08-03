"""Phase 30-02 Visual Bible owner scope, candidate-only and evidence/authority
integration tests (REQ-VIS-01 / D-30-01..D-30-04).

Covers the 30-VALIDATION.md integration matrix:
- cross-owner/version/source-snapshot access fails closed (no owner leak);
- candidate revisions expose evidence, authority labels, review state and
  rights/provenance; approval is append-only, explicit and idempotent;
- wrong snapshot/slice hashes, spoiler cutoffs, conflicting retries and
  no-evidence canon claims fail closed and never promote anything;
- approval never rewrites the source chapter and generated reference assets
  never silently become canon.
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
from app.models.visual_bible import (
    VisualBibleReviewEvent,
    VisualBibleVersion,
    VisualClaim,
    VisualEntity,
    VisualEvidenceRef,
    VisualReferenceAsset,
)
from app.schemas.visual_bible import (
    VisualBibleVersionContract,
    VisualClaimContract,
    claim_content_hash,
    recompute_manifest_hash,
)
from app.services.visual_bible.authority import VisualBibleAuthorityService
from app.services.visual_bible.evidence import (
    ChapterRecord,
    VisualBibleEvidenceService,
    compute_source_snapshot_hash,
)

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64

CH1 = "Ayla was a tall young woman with braided amber hair and green eyes. She wore a grey wool cloak."
CH2 = "The stone hall of the northern keep stood cold; its tall windows let in pale light."


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_owner(
    sync_url: str, *, suffix: str, contents: tuple[str, ...] | None = None
) -> dict[str, Any]:
    contents = contents or (CH1, CH2)
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"vb_{suffix}",
            email=f"vb_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"VB Novel {suffix}",
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


def _snapshot_hash(
    owner_id: int, novel_id: int, chapter_ids: list[int], contents: list[str]
) -> str:
    chapters = [
        ChapterRecord(
            chapter_id=chapter_id,
            chapter_number=i + 1,
            content=content,
        )
        for i, (chapter_id, content) in enumerate(zip(chapter_ids, contents))
    ]
    return compute_source_snapshot_hash(
        owner_id=owner_id, novel_id=novel_id, chapters=chapters
    )


# ---------------------------------------------------------------------------
# Contract builders (mirror tests/unit/visual_bible/test_contracts.py builders)
# ---------------------------------------------------------------------------


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
    content_hash: str | None = None,
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
        "content_hash": content_hash or _sha256(content[start:end]),
        "cutoff_chapter": cutoff_chapter,
    }


def _claim_payload(
    *,
    claim_key: str,
    entity_stable_id: str,
    authority: str,
    description: str,
    cutoff_chapter: int,
    evidence: list[dict[str, Any]] | None = None,
    author: str | None = None,
    rationale: str | None = None,
) -> dict[str, Any]:
    payload = {
        "claim_key": claim_key,
        "entity_stable_id": entity_stable_id,
        "authority": authority,
        "description": description,
        "author": author,
        "rationale": rationale,
        "cutoff_chapter": cutoff_chapter,
        "claim_hash": "0" * 64,
        "evidence_refs": evidence or [],
    }
    claim = VisualClaimContract.model_validate(payload)
    claim = claim.model_copy(update={"claim_hash": claim_content_hash(claim)})
    return claim.model_dump(mode="json")


def _entity_payload(
    *,
    stable_id: str,
    entity_type: str,
    description: str,
    authority: str,
    disclosure_cutoff: int,
) -> dict[str, Any]:
    return {
        "stable_id": stable_id,
        "entity_key": stable_id,
        "entity_type": entity_type,
        "description": description,
        "authority": authority,
        "disclosure_cutoff": disclosure_cutoff,
    }


def _asset_payload(
    *,
    asset_key: str,
    rights_status: str = "unreviewed",
    asset_id: str = "obj-1",
) -> dict[str, Any]:
    return {
        "asset_key": asset_key,
        "asset_id": asset_id,
        "mime_type": "image/png",
        "bytes_hash": HEX64_B,
        "rights_status": rights_status,
        "provenance": {"source": "user-upload", "license": "pending"},
    }


def build_version_contract(
    *,
    owner_id: int,
    novel_id: int,
    source_snapshot_id: str,
    snapshot_hash: str,
    cutoff_chapter: int,
    entities: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    assets: list[dict[str, Any]] | None = None,
    version_key: str = "vb-main",
) -> VisualBibleVersionContract:
    payload = {
        "schema_version": "visual-bible.v1",
        "artifact_kind": "visual_bible",
        "owner_id": owner_id,
        "novel_id": novel_id,
        "version_key": version_key,
        "revision_number": 1,
        "parent_version_id": None,
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_hash": snapshot_hash,
        "cutoff_chapter": cutoff_chapter,
        "schema_hash": HEX64,
        "policy_hash": HEX64_B,
        "prompt_hash": HEX64_C,
        "model_hash": None,
        "config_hash": None,
        "manifest_hash": "0" * 64,
        "style_profile": None,
        "constraints": None,
        "entities": entities,
        "claims": claims,
        "reference_assets": assets or [],
        "review_state": "candidate",
    }
    version = VisualBibleVersionContract.model_validate(payload)
    version = version.model_copy(
        update={"manifest_hash": recompute_manifest_hash(version)}
    )
    return version


def basic_version_payload(
    ids: dict[str, Any],
    *,
    source_snapshot_id: str = "ss-1",
    cutoff: int = 2,
    version_key: str = "vb-main",
) -> dict[str, Any]:
    """vb-basic-v1: character/place canon claims + style interpretation."""
    snapshot_hash = _snapshot_hash(
        ids["owner_id"], ids["novel_id"], ids["chapter_ids"], ids["contents"]
    )
    ch1, ch2 = ids["contents"]
    ch1_id, ch2_id = ids["chapter_ids"]
    entities = [
        _entity_payload(
            stable_id="char-ayla",
            entity_type="character",
            description="A tall young woman with amber hair.",
            authority="canon_fact",
            disclosure_cutoff=2,
        ),
        _entity_payload(
            stable_id="place-hall",
            entity_type="place",
            description="A cold stone hall with tall windows.",
            authority="canon_fact",
            disclosure_cutoff=2,
        ),
        _entity_payload(
            stable_id="style-voice",
            entity_type="style",
            description="Sparse, weather-driven prose.",
            authority="literary_interpretation",
            disclosure_cutoff=2,
        ),
    ]
    claims = [
        _claim_payload(
            claim_key="char-ayla-hair",
            entity_stable_id="char-ayla",
            authority="canon_fact",
            description="amber braided hair and green eyes",
            cutoff_chapter=cutoff,
            evidence=[
                _evidence_payload(
                    content=ch1,
                    find_text="amber hair",
                    evidence_key="ev-hair",
                    chapter_id=ch1_id,
                    chapter_number=1,
                    source_snapshot_id=source_snapshot_id,
                    source_snapshot_hash=snapshot_hash,
                    cutoff_chapter=cutoff,
                )
            ],
        ),
        _claim_payload(
            claim_key="place-hall-windows",
            entity_stable_id="place-hall",
            authority="canon_fact",
            description="tall windows in the stone hall",
            cutoff_chapter=cutoff,
            evidence=[
                _evidence_payload(
                    content=ch2,
                    find_text="tall windows",
                    evidence_key="ev-windows",
                    chapter_id=ch2_id,
                    chapter_number=2,
                    source_snapshot_id=source_snapshot_id,
                    source_snapshot_hash=snapshot_hash,
                    cutoff_chapter=cutoff,
                )
            ],
        ),
        _claim_payload(
            claim_key="style-voice",
            entity_stable_id="style-voice",
            authority="literary_interpretation",
            description="sparse weather-driven prose",
            cutoff_chapter=cutoff,
            author="critic",
            rationale="consistent chapter pacing",
            evidence=[],
        ),
    ]
    assets = [_asset_payload(asset_key="ref-ayla-sketch", rights_status="pending")]
    version = build_version_contract(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        source_snapshot_id=source_snapshot_id,
        snapshot_hash=snapshot_hash,
        cutoff_chapter=cutoff,
        entities=entities,
        claims=claims,
        assets=assets,
        version_key=version_key,
    )
    return version.model_dump(mode="json")


def _review_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "action": "approve",
        "actor_source": "human",
        "actor": "reader",
        "reason": "matches the text",
        "event_key": "ev-approve-1",
        "from_review_state": "candidate",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Owner scope / IDOR matrix
# ---------------------------------------------------------------------------


async def test_cross_owner_visual_bible_matrix_404(api_client):
    client, _, sync_url = api_client
    ids_a = _seed_owner(sync_url, suffix=f"a_{uuid.uuid4().hex[:8]}")
    ids_b = _seed_owner(sync_url, suffix=f"b_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {ids_a['token']}"}
    headers_b = {"Authorization": f"Bearer {ids_b['token']}"}

    base_a = f"/api/novels/{ids_a['novel_id']}/visual-bible"
    created = await client.post(
        base_a, json={"version": basic_version_payload(ids_a)}, headers=headers_a
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["version"]["id"]

    # Owner B probing owner A's novel: every route is an identical 404.
    foreign_list = await client.get(base_a, headers=headers_b)
    assert foreign_list.status_code == 404
    foreign_detail = await client.get(
        f"{base_a}/{version_id}", headers=headers_b
    )
    assert foreign_detail.status_code == 404
    foreign_create = await client.post(
        base_a, json={"version": {}}, headers=headers_b
    )
    assert foreign_create.status_code == 404
    foreign_review = await client.post(
        f"{base_a}/{version_id}/review",
        json=_review_payload(event_key="steal"),
        headers=headers_b,
    )
    assert foreign_review.status_code == 404

    missing_novel = await client.get(
        "/api/novels/999999991/visual-bible", headers=headers_b
    )
    assert missing_novel.status_code == 404
    assert foreign_list.json() == missing_novel.json()

    # B sees an empty list for B's own novel, and A still reads A's version.
    ok_b = await client.get(
        f"/api/novels/{ids_b['novel_id']}/visual-bible", headers=headers_b
    )
    assert ok_b.status_code == 200
    assert ok_b.json()["total"] == 0
    ok_a = await client.get(base_a, headers=headers_a)
    assert ok_a.json()["total"] == 1


async def test_unauthenticated_visual_bible_routes_reject(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"anon_{uuid.uuid4().hex[:8]}")
    base = f"/api/novels/{ids['novel_id']}/visual-bible"
    resp = await client.get(base)
    assert resp.status_code == 401
    resp2 = await client.post(base, json={"version": {}})
    assert resp2.status_code == 401


# ---------------------------------------------------------------------------
# Candidate creation exposes evidence + authority + rights (candidate-only)
# ---------------------------------------------------------------------------


async def test_create_candidate_exposes_evidence_authority_and_rights(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cand_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"

    resp = await client.post(
        base, json={"version": basic_version_payload(ids)}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replayed"] is False
    version = body["version"]
    assert version["review_state"] == "candidate"
    assert version["owner_id"] == ids["owner_id"]
    assert version["novel_id"] == ids["novel_id"]
    assert version["cutoff_chapter"] == 2

    entities = {e["stable_id"]: e for e in version["entities"]}
    assert set(entities) == {"char-ayla", "place-hall", "style-voice"}
    assert entities["char-ayla"]["entity_type"] == "character"
    assert entities["char-ayla"]["authority"] == "canon_fact"

    # Canon claim carries its evidence ref with snapshot/cutoff/hash lineage.
    canon_claim = entities["char-ayla"]["claims"][0]
    assert canon_claim["authority"] == "canon_fact"
    assert canon_claim["cutoff_chapter"] == 2
    assert len(canon_claim["claim_hash"]) == 64
    ref = canon_claim["evidence_refs"][0]
    assert ref["evidence_key"] == "ev-hair"
    assert ref["source_snapshot_id"] == "ss-1"
    assert ref["source_snapshot_hash"] == version["source_snapshot_hash"]
    assert ref["cutoff_chapter"] == 2
    assert len(ref["content_hash"]) == 64

    # Interpretation claim keeps its author/rationale and is never collapsed.
    style_claim = entities["style-voice"]["claims"][0]
    assert style_claim["authority"] == "literary_interpretation"
    assert style_claim["author"] == "critic"
    assert style_claim["rationale"]

    # Rights/provenance stays visible but never approved.
    assert version["reference_assets"][0]["rights_status"] == "pending"
    assert version["reference_assets"][0]["approved"] is False


# ---------------------------------------------------------------------------
# Fail-closed evidence gates
# ---------------------------------------------------------------------------


async def test_wrong_snapshot_hash_fails_closed_with_reason(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"hash_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"
    payload = {"version": basic_version_payload(ids)}

    # Corrupt the snapshot hash on the version and every evidence ref.
    payload["version"]["source_snapshot_hash"] = HEX64
    for claim in payload["version"]["claims"]:
        for ref in claim["evidence_refs"]:
            ref["source_snapshot_hash"] = HEX64

    resp = await client.post(base, json=payload, headers=headers)
    assert resp.status_code == 409, resp.text
    assert resp.json()["kind"] == "visual_bible_unresolved"
    codes = {item["claim_key"]: item["reason_code"] for item in resp.json()["unresolved"]}
    assert codes["char-ayla-hair"] == "stale_snapshot_lineage"
    assert codes["place-hall-windows"] == "stale_snapshot_lineage"


async def test_wrong_evidence_content_hash_fails_closed_with_reason(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"slice_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"
    payload = {"version": basic_version_payload(ids)}

    claim = next(
        c for c in payload["version"]["claims"] if c["claim_key"] == "char-ayla-hair"
    )
    claim["evidence_refs"][0]["content_hash"] = HEX64  # wrong slice hash

    resp = await client.post(base, json=payload, headers=headers)
    assert resp.status_code == 409, resp.text
    unresolved = {u["claim_key"]: u["reason_code"] for u in resp.json()["unresolved"]}
    assert unresolved["char-ayla-hair"] == "evidence_content_mismatch"


async def test_evidence_cutoff_mismatch_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cutoff_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"
    payload = {"version": basic_version_payload(ids)}

    # Ref is internally consistent (chapter 1 <= ref cutoff 1) but the version
    # spoiler cutoff is 2, so the version-level lineage gate must reject it.
    claim = next(
        c for c in payload["version"]["claims"] if c["claim_key"] == "char-ayla-hair"
    )
    claim["evidence_refs"][0]["cutoff_chapter"] = 1

    resp = await client.post(base, json=payload, headers=headers)
    assert resp.status_code == 409, resp.text
    unresolved = {u["claim_key"]: u["reason_code"] for u in resp.json()["unresolved"]}
    assert unresolved["char-ayla-hair"] == "evidence_lineage_mismatch"


async def test_canon_claim_without_evidence_is_rejected_by_contract(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"noev_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"
    payload = {"version": basic_version_payload(ids)}

    payload["version"]["claims"].append(
        {
            "claim_key": "char-ayla-noevidence",
            "entity_stable_id": "char-ayla",
            "authority": "canon_fact",
            "description": "no source at all",
            "author": None,
            "rationale": None,
            "cutoff_chapter": 2,
            "claim_hash": "0" * 64,
            "evidence_refs": [],
        }
    )

    resp = await client.post(base, json=payload, headers=headers)
    assert resp.status_code == 422, resp.text  # strict contract fails closed


# ---------------------------------------------------------------------------
# Idempotent candidate creation and immutable lineage
# ---------------------------------------------------------------------------


async def test_identical_create_replays_same_revision(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"replay_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"
    payload = {"version": basic_version_payload(ids)}

    first = await client.post(base, json=payload, headers=headers)
    assert first.status_code == 201
    second = await client.post(base, json=payload, headers=headers)
    assert second.status_code == 201
    assert second.json()["replayed"] is True
    assert second.json()["version"]["id"] == first.json()["version"]["id"]

    listing = await client.get(base, headers=headers)
    assert listing.json()["total"] == 1


async def test_conflicting_create_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"conflict_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"
    payload = {"version": basic_version_payload(ids)}

    assert (await client.post(base, json=payload, headers=headers)).status_code == 201

    # Same version_key, but different immutable claim content.
    changed = {"version": basic_version_payload(ids)}
    claim = next(
        c for c in changed["version"]["claims"] if c["claim_key"] == "char-ayla-hair"
    )
    claim["description"] = "raven-black hair and grey eyes"
    claim["claim_hash"] = "0" * 64
    # Recompute the claim + manifest hashes so only the immutable conflict remains.
    rebuilt = VisualBibleVersionContract.model_validate(changed["version"])
    new_claim = rebuilt.claims[0].model_copy(
        update={"claim_hash": claim_content_hash(rebuilt.claims[0])}
    )
    rebuilt = rebuilt.model_copy(
        update={"claims": [new_claim, *rebuilt.claims[1:]]}
    )
    changed["version"] = rebuilt.model_copy(
        update={"manifest_hash": recompute_manifest_hash(rebuilt)}
    ).model_dump(mode="json")

    resp = await client.post(base, json=changed, headers=headers)
    assert resp.status_code == 409, resp.text
    assert "version_key" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Append-only, explicit, idempotent review
# ---------------------------------------------------------------------------


async def test_approval_is_append_only_and_idempotent(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"review_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"

    created = await client.post(
        base, json={"version": basic_version_payload(ids)}, headers=headers
    )
    assert created.status_code == 201
    version_id = created.json()["version"]["id"]
    review_url = f"{base}/{version_id}/review"

    approved = await client.post(
        review_url, json=_review_payload(), headers=headers
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_state"] == "approved"
    assert len(approved.json()["review_events"]) == 1

    # Retry with the same event_key (original from_review_state) is idempotent.
    retry = await client.post(review_url, json=_review_payload(), headers=headers)
    assert retry.status_code == 200
    assert retry.json()["review_state"] == "approved"
    assert len(retry.json()["review_events"]) == 1  # no second approval

    # Stale from_review_state is rejected: candidate is no longer current.
    stale = await client.post(
        review_url,
        json=_review_payload(
            from_review_state="candidate", event_key="ev-approve-2"
        ),
        headers=headers,
    )
    assert stale.status_code == 409, stale.text

    # Illegal transition: approving an approved revision is rejected.
    illegal = await client.post(
        review_url,
        json=_review_payload(
            action="approve",
            from_review_state="approved",
            event_key="ev-approve-3",
        ),
        headers=headers,
    )
    assert illegal.status_code == 409, illegal.text

    # Supersede from approved is the only legal forward action.
    superseded = await client.post(
        review_url,
        json=_review_payload(
            action="supersede",
            from_review_state="approved",
            event_key="ev-supersede-1",
        ),
        headers=headers,
    )
    assert superseded.status_code == 200
    assert superseded.json()["review_state"] == "superseded"
    assert {e["action"] for e in superseded.json()["review_events"]} == {
        "approve",
        "supersede",
    }


async def test_approval_never_touches_asset_or_source_chapter(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"immut_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"

    created = await client.post(
        base, json={"version": basic_version_payload(ids)}, headers=headers
    )
    version_id = created.json()["version"]["id"]
    await client.post(
        f"{base}/{version_id}/review", json=_review_payload(), headers=headers
    )
    detail = await client.get(f"{base}/{version_id}", headers=headers)
    assert detail.json()["review_state"] == "approved"
    assert detail.json()["reference_assets"][0]["approved"] is False

    # The authoritative chapter body is untouched by creation or approval.
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        stored = session.get(Chapter, ids["chapter_ids"][0])
        assert stored.content == ids["contents"][0]
    engine.dispose()


# ---------------------------------------------------------------------------
# Service-level evidence/authority seam
# ---------------------------------------------------------------------------


async def test_evidence_service_materializes_valid_claim(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"svc_{uuid.uuid4().hex[:8]}")
    version = VisualBibleVersionContract.model_validate(basic_version_payload(ids))

    async with factory() as session:
        service = VisualBibleEvidenceService(session)
        outcome = await service.materialize_version_claims(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            source_snapshot_id=version.source_snapshot_id,
            source_snapshot_hash=version.source_snapshot_hash,
            cutoff_chapter=version.cutoff_chapter,
            claims=version.claims,
        )
        assert not outcome.blocked
        by_key = {m.claim.claim_key: m for m in outcome.resolved}
        assert "char-ayla-hair" in by_key
        assert len(by_key["char-ayla-hair"].verified_evidence) == 1
        # interpretation claims never need leaf evidence
        assert len(by_key["style-voice"].verified_evidence) == 0
        await session.rollback()


async def test_evidence_service_returns_reason_coded_unresolved(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"unres_{uuid.uuid4().hex[:8]}")
    version = VisualBibleVersionContract.model_validate(basic_version_payload(ids))
    version = version.model_copy(update={"source_snapshot_hash": HEX64})

    async with factory() as session:
        service = VisualBibleEvidenceService(session)
        outcome = await service.materialize_version_claims(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            source_snapshot_id=version.source_snapshot_id,
            source_snapshot_hash=version.source_snapshot_hash,
            cutoff_chapter=version.cutoff_chapter,
            claims=version.claims,
        )
        assert outcome.blocked
        codes = {u.claim_key: u.reason_code for u in outcome.unresolved}
        assert codes["char-ayla-hair"] == "stale_snapshot_lineage"
        await session.rollback()


async def test_authority_service_persists_rows_and_replays(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"auth_{uuid.uuid4().hex[:8]}")
    version = VisualBibleVersionContract.model_validate(basic_version_payload(ids))

    async with factory() as session:
        evidence = VisualBibleEvidenceService(session)
        outcome = await evidence.materialize_version_claims(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            source_snapshot_id=version.source_snapshot_id,
            source_snapshot_hash=version.source_snapshot_hash,
            cutoff_chapter=version.cutoff_chapter,
            claims=version.claims,
        )
        assert not outcome.blocked
        verified = {m.claim.claim_key: m.verified_evidence for m in outcome.resolved}

        authority = VisualBibleAuthorityService(session)
        persisted = await authority.create_revision(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version=version,
            verified_evidence=verified,
        )
        assert persisted.replayed is False
        version_id = persisted.version.id

        replay = await authority.create_revision(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version=version,
            verified_evidence=verified,
        )
        assert replay.replayed is True
        assert replay.version.id == version_id

        assert (
            await session.scalar(
                select(VisualEntity).where(
                    VisualEntity.version_id == version_id
                )
            )
            is not None
        )
        assert (
            await session.scalar(
                select(VisualClaim).where(VisualClaim.version_id == version_id)
            )
            is not None
        )
        assert (
            await session.scalar(
                select(VisualEvidenceRef).where(
                    VisualEvidenceRef.version_id == version_id
                )
            )
            is not None
        )
        assert (
            await session.scalar(
                select(VisualReferenceAsset).where(
                    VisualReferenceAsset.version_id == version_id
                )
            )
            is not None
        )

        # Cross-owner read of the same version_id fails closed.
        assert (
            await session.scalar(
                select(VisualBibleVersion).where(
                    VisualBibleVersion.owner_id == ids["owner_id"] + 1,
                    VisualBibleVersion.novel_id == ids["novel_id"],
                    VisualBibleVersion.id == version_id,
                )
            )
            is None
        )
        await session.rollback()


async def test_authority_service_review_event_is_append_only(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"revsvc_{uuid.uuid4().hex[:8]}")
    version = VisualBibleVersionContract.model_validate(basic_version_payload(ids))

    async with factory() as session:
        evidence = VisualBibleEvidenceService(session)
        outcome = await evidence.materialize_version_claims(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            source_snapshot_id=version.source_snapshot_id,
            source_snapshot_hash=version.source_snapshot_hash,
            cutoff_chapter=version.cutoff_chapter,
            claims=version.claims,
        )
        verified = {m.claim.claim_key: m.verified_evidence for m in outcome.resolved}
        authority = VisualBibleAuthorityService(session)
        persisted = await authority.create_revision(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version=version,
            verified_evidence=verified,
        )
        version_id = persisted.version.id

        from app.schemas.visual_bible import (
            VisualActorSource,
            VisualReviewEventInput,
            VisualReviewState,
        )

        event = VisualReviewEventInput(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=version_id,
            action="approve",
            actor_source=VisualActorSource.HUMAN,
            actor="reader",
            reason="matches the text",
            event_key="ev-svc-approve",
            from_review_state=VisualReviewState.CANDIDATE,
        )
        approved = await authority.apply_review(
            owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=event
        )
        assert approved.review_state == "approved"
        event_count = (
            await session.scalar(
                select(VisualBibleReviewEvent.id).where(
                    VisualBibleReviewEvent.version_id == version_id
                )
            )
            is not None
        )
        assert event_count
        await session.rollback()
