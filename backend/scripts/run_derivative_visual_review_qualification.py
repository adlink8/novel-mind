"""Phase 38-04 derivative visual review browser qualification seed.

Seeds the full fixture matrix from 38-VALIDATION.md for the real-browser
``derivative-visual.spec.ts``:

- an Original Visual Bible snapshot (immutable reference);
- an approved derivative visual fork + frozen derivative Scene Specs;
- one consistent identity across three chapters (two approved, one rejected),
  one identity-drift candidate (``blocked`` — never publishable), and a
  declared-style-divergence identity (``needs_review``);
- every candidate persists its generated asset_id, checksum, divergence
  manifest and the deterministic cross-chapter consistency report.

Usage::

    python scripts/run_derivative_visual_review_qualification.py \\
        --e2e-seed-user <username>

prints ``E2E_RESULT=<json>`` with the owner/novel ids and the candidate
asset ids by state. Re-running for the same user replays the same rows.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64


def _content_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _png_bytes() -> bytes:
    return bytes.fromhex("89504e470d0a1a0a0000000000000000")


def _idem64() -> str:
    return uuid.uuid4().hex * 2


async def _seed_owner(session, owner) -> dict:
    from sqlalchemy import select

    from app.models.canon_fork import CanonFork
    from app.models.derivative_project import DerivativeProject
    from app.models.novel import Novel
    from app.models.visual_bible import VisualBibleVersion

    novel = Novel(
        title=f"Derivative Review {uuid.uuid4().hex[:6]}",
        owner_id=owner.id,
        status="ready",
        reading_progress={},
        chapter_count=6,
        word_count=6,
    )
    session.add(novel)
    await session.flush()

    fork = CanonFork(
        owner_id=owner.id,
        novel_id=novel.id,
        fork_key=f"ff-dv-{uuid.uuid4().hex[:8]}",
        space="fanfiction_canon",
        status="approved",
        source_version_key="original:1",
        source_snapshot_id="snap-1",
        source_snapshot_hash=HEX64,
        through_chapter=8,
        full_book_authorized=False,
        cutoff_snapshot_hash=HEX64,
        scope_hash=HEX64,
        manifest_hash=HEX64,
        citation_lineage=[],
        authorization={},
        active=False,
    )
    session.add(fork)
    await session.flush()

    project = DerivativeProject(
        owner_id=owner.id,
        novel_id=novel.id,
        fork_id=fork.id,
        project_key=f"proj-{uuid.uuid4().hex[:8]}",
        name="Visual Review Project",
        status="active",
        space="fanfiction_canon",
        fork_key=fork.fork_key,
        source_version_key="original:1",
        source_snapshot_hash=HEX64,
        through_chapter=8,
        full_book_authorized=False,
        cutoff_snapshot_hash=HEX64,
        scope_hash=HEX64,
        manifest_hash=HEX64,
    )
    session.add(project)
    await session.flush()

    original = VisualBibleVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key=f"vb-original-{uuid.uuid4().hex[:8]}",
        revision_number=1,
        source_snapshot_id="snap-1",
        source_snapshot_hash=HEX64,
        cutoff_chapter=8,
        review_state="candidate",
        schema_version="visual-bible.v1",
        schema_hash=HEX64,
        policy_hash=HEX64_B,
        manifest_hash=HEX64_C,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=_idem64(),
        projection_hash=HEX64,
    )
    session.add(original)
    await session.flush()
    return {
        "owner_id": owner.id,
        "novel_id": novel.id,
        "fork_id": fork.id,
        "project_id": project.id,
        "source_version_id": original.id,
    }


def _fork_payload(ids: dict, *, version_key: str) -> "DerivativeVisualVersionContract":
    from app.schemas.derivative_visual import (
        DerivativeVisualVersionContract,
        recompute_derivative_visual_manifest_hash,
    )

    payload = {
        "schema_version": "derivative-visual.v1",
        "namespace": "fanfiction_visual",
        "owner_id": ids["owner_id"],
        "novel_id": ids["novel_id"],
        "project_id": ids["project_id"],
        "fork_id": ids["fork_id"],
        "version_key": version_key,
        "revision_number": 1,
        "source_version_id": ids["source_version_id"],
        "source_snapshot_id": "snap-1",
        "source_snapshot_hash": HEX64,
        "source_manifest_hash": HEX64_C,
        "cutoff_chapter": 8,
        "divergence": {"style": "warm palette", "note": "branch A"},
        "provenance": {"branch": "fork-1", "project_key": "proj"},
        "schema_hash": HEX64,
        "policy_hash": HEX64_B,
        "manifest_hash": "0" * 64,
        "entities": [
            {
                "stable_id": "char-arya",
                "entity_key": "char-arya",
                "entity_type": "character",
                "description": "grey-eyed archer",
                "authority": "canon_fact",
                "divergence": {"palette": "soft greys"},
                "source_entity_ref": {
                    "source_entity_id": 7,
                    "source_entity_key": "char-arya",
                    "source_entity_hash": HEX64,
                },
                "disclosure_cutoff": 8,
            }
        ],
        "reference_assets": [
            {
                "asset_key": "dv-arya",
                "asset_id": "dv-obj-1",
                "mime_type": "image/png",
                "bytes_hash": HEX64_B,
                "source_asset_ref": {
                    "source_asset_id": "obj-1",
                    "source_bytes_hash": HEX64_B,
                },
            }
        ],
    }
    version = DerivativeVisualVersionContract.model_validate(payload)
    return version.model_copy(
        update={"manifest_hash": recompute_derivative_visual_manifest_hash(version)}
    )


def _spec_payload(
    ids: dict,
    version,
    *,
    spec_key: str,
    chapter_number: int,
    stable_id: str,
    entity_key: str,
    identity_source_hash: str,
    style_profile: dict,
    divergence: dict,
    identity_divergence: dict,
) -> dict:
    return {
        "schema_version": "derivative-scene-spec.v1",
        "artifact_kind": "derivative_scene_spec",
        "owner_id": ids["owner_id"],
        "novel_id": ids["novel_id"],
        "project_id": ids["project_id"],
        "fork_id": ids["fork_id"],
        "visual_namespace": "fanfiction_visual",
        "spec_key": spec_key,
        "revision_number": 1,
        "visual_fork_version_id": version.id,
        "visual_fork_version_hash": version.canonical_payload_hash,
        "scene_spec_id": None,
        "scene_spec_hash": HEX64,
        "scene_candidate_hash": HEX64,
        "visual_bible_revision_id": ids["source_version_id"],
        "visual_bible_revision_hash": HEX64_C,
        "source_snapshot_id": "snap-1",
        "source_snapshot_hash": HEX64,
        "source_manifest_hash": HEX64_C,
        "cutoff_chapter": 8,
        "divergence": divergence,
        "provenance": {"branch": "fork-1", "project": "proj-1"},
        "identity": [
            {
                "stable_id": stable_id,
                "entity_key": entity_key,
                "entity_type": "character",
                "description": "derivative character",
                "authority": "canon_fact",
                "divergence": identity_divergence,
                "source_entity_ref": {
                    "source_entity_id": 7,
                    "source_entity_key": entity_key,
                    "source_entity_hash": identity_source_hash,
                },
                "disclosure_cutoff": 8,
            }
        ],
        "style_profile": style_profile,
        "negative_constraints": [],
        "reference_assets": [
            {
                "asset_key": "dv-arya",
                "asset_id": "dv-obj-1",
                "mime_type": "image/png",
                "bytes_hash": HEX64_B,
                "rights_status": "unreviewed",
                "source_asset_ref": {
                    "source_asset_id": "obj-1",
                    "source_bytes_hash": HEX64_B,
                },
                "approved": False,
            }
        ],
        "asset_lineage": [],
        "anchors": [],
        "evidence_refs": [
            {
                "evidence_key": f"ev-{spec_key}",
                "source_snapshot_id": "snap-1",
                "source_snapshot_hash": HEX64,
                "chapter_number": chapter_number,
                "source_start": 10,
                "source_end": 40,
                "content_hash": HEX64_B,
                "cutoff_chapter": 8,
            }
        ],
        "uncertainties": [],
        "export_manifest_hash": None,
        "content_hash": "0" * 64,
        "review_state": "candidate",
    }


def _make_spec(ids, version, **kwargs) -> "DerivativeSceneSpecContract":
    from app.schemas.derivative_visual import (
        DerivativeIdentityRow,
        DerivativeReferenceAssetRow,
        DerivativeSceneSpecContract,
        DerivativeSceneSpecEvidenceRef,
        recompute_derivative_scene_spec_hash,
    )

    payload = _spec_payload(ids, version, **kwargs)
    draft = DerivativeSceneSpecContract.model_construct(
        identity=[DerivativeIdentityRow.model_validate(row) for row in payload["identity"]],
        reference_assets=[
            DerivativeReferenceAssetRow.model_validate(row)
            for row in payload["reference_assets"]
        ],
        evidence_refs=[
            DerivativeSceneSpecEvidenceRef.model_validate(row)
            for row in payload["evidence_refs"]
        ],
        negative_constraints=[],
        asset_lineage=[],
        anchors=[],
        uncertainties=[],
        **{
            key: value
            for key, value in payload.items()
            if key
            not in {
                "identity",
                "reference_assets",
                "evidence_refs",
                "negative_constraints",
                "asset_lineage",
                "anchors",
                "uncertainties",
            }
        },
    )
    spec = draft.model_copy(
        update={"content_hash": recompute_derivative_scene_spec_hash(draft)}
    )
    return DerivativeSceneSpecContract.model_validate(spec.model_dump())


def _candidate_write(
    spec,
    *,
    asset_key: str,
    chapter_number: int,
    content_hash: str,
) -> "DerivativeAssetCandidateWrite":
    from app.schemas.derivative_visual_asset import (
        DerivativeAssetCandidateWrite,
        DerivativeAssetIdentityRow as CandidateIdentityRow,
        DerivativeAssetSourceRef,
        divergence_manifest_hash_from_spec,
    )

    identity = spec.identity[0]
    source_ref = spec.reference_assets[0]
    return DerivativeAssetCandidateWrite(
        asset_key=asset_key,
        chapter_number=chapter_number,
        mime_type="image/png",
        content_hash=content_hash,
        scene_spec_hash=spec.content_hash,
        divergence_manifest_hash=divergence_manifest_hash_from_spec(spec),
        identity_lineage=[
            CandidateIdentityRow(
                stable_id=identity.stable_id,
                entity_key=identity.entity_key,
                entity_type=identity.entity_type.value,
                source_entity_hash=str(identity.source_entity_ref["source_entity_hash"]),
            )
        ],
        source_refs=[
            DerivativeAssetSourceRef(
                asset_key=source_ref.asset_key,
                asset_id=source_ref.asset_id,
                source_asset_id=source_ref.source_asset_ref["source_asset_id"],
                source_bytes_hash=source_ref.source_asset_ref["source_bytes_hash"],
            )
        ],
        generator_lineage={"provider": "mock", "provider_model": "mock-1"},
    )


async def _store_candidate(session, storage, ids, version, spec, asset_key, chapter) -> tuple:
    from app.services.derivative_visual.assets import store_derivative_candidate_asset

    payload = bytes([chapter]) * 8
    candidate = _candidate_write(
        spec,
        asset_key=asset_key,
        chapter_number=chapter,
        content_hash=_content_hash(payload),
    )
    row, _ = await store_derivative_candidate_asset(
        session,
        storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        spec=spec,
        candidate=candidate,
        payload=payload,
    )
    await session.flush()
    return row, candidate, payload


async def _apply_candidate_review(session, ids, row, action: str, event_key: str) -> None:
    from app.schemas.derivative_visual_asset import (
        DerivativeAssetReviewEventInput,
    )
    from app.services.derivative_visual.assets import apply_derivative_asset_review

    event = DerivativeAssetReviewEventInput(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        candidate_id=row.id,
        action=action,
        actor_source="human",
        actor="seed-script",
        reason=f"browser seed {action}",
        event_key=event_key,
        from_review_state=row.review_state,
    )
    await apply_derivative_asset_review(
        session, owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=event
    )
    await session.flush()


async def seed_browser_review(username: str) -> dict:
    """Seed the derivative visual review fixture matrix for one browser owner."""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.user import User
    from app.schemas.derivative_visual import DerivativeVisualReviewEventInput
    from app.services.derivative_visual.assets import DerivativeAssetStorage
    from app.services.derivative_visual.fork import create_derivative_visual_fork
    from app.services.derivative_visual.lineage import apply_review as apply_fork_review

    async with async_session_factory.begin() as session:
        owner = await session.scalar(select(User).where(User.username == username))
        if owner is None:
            raise ValueError(f"browser owner {username!r} does not exist")

        ids = await _seed_owner(session, owner)
        fork_result = await create_derivative_visual_fork(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version=_fork_payload(ids, version_key="dv-review-browser"),
        )
        await session.flush()
        version = fork_result.version
        # Approve the fork version (anchors candidate storage, D-38-03).
        await apply_fork_review(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            event=DerivativeVisualReviewEventInput(
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                version_id=version.id,
                action="approve",
                actor_source="human",
                actor="seed-script",
                reason="browser seed fork approval",
                event_key=f"ev-approve-fork-{version.id}",
                from_review_state="candidate",
            ),
        )
        await session.flush()

        storage = DerivativeAssetStorage(DerivativeAssetStorage.default_storage_root())

        warm = {"palette": "warm"}
        cold = {"palette": "cold"}
        declared = {"style": "cold palette", "note": "declared"}
        branch_div = {"style": "warm palette", "note": "branch A"}

        # Group A: consistent identity across chapters 1..3 (char-arya).
        specs = [
            _make_spec(
                ids,
                version,
                spec_key="ds-1",
                chapter_number=1,
                stable_id="char-arya",
                entity_key="char-arya",
                identity_source_hash=HEX64,
                style_profile=warm,
                divergence=branch_div,
                identity_divergence={"palette": "soft greys"},
            ),
            _make_spec(
                ids,
                version,
                spec_key="ds-2",
                chapter_number=2,
                stable_id="char-arya",
                entity_key="char-arya",
                identity_source_hash=HEX64,
                style_profile=warm,
                divergence=branch_div,
                identity_divergence={"palette": "soft greys"},
            ),
            _make_spec(
                ids,
                version,
                spec_key="ds-3",
                chapter_number=3,
                stable_id="char-arya",
                entity_key="char-arya",
                identity_source_hash=HEX64,
                style_profile=warm,
                divergence=branch_div,
                identity_divergence={"palette": "soft greys"},
            ),
        ]
        rows = []
        # Store ch-2/ch-3 first so ch-1's cross-chapter consistency check (run
        # at store time) sees two siblings and scores "pass". Storing ch-1
        # first would freeze verdict=unavailable (no siblings exist yet).
        store_order = [
            (specs[1], "ch-2", 2),
            (specs[2], "ch-3", 3),
            (specs[0], "ch-1", 1),
        ]
        for spec, asset_key, chapter in store_order:
            row, _, _ = await _store_candidate(
                session, storage, ids, version, spec, asset_key, chapter
            )
            rows.append(row)
        # Reorder rows to chapter order so the review events below bind the
        # right candidate (queue is chapter_number-ordered).
        rows.sort(key=lambda r: r.chapter_number)
        # Deterministic states: ch-1 needs_review, ch-2/3 candidate (pass).
        # ch-1 approved, ch-2 rejected, ch-3 approved.
        await _apply_candidate_review(session, ids, rows[0], "approve", "ev-b-approve-1")
        await _apply_candidate_review(session, ids, rows[1], "reject", "ev-b-reject-2")
        await _apply_candidate_review(session, ids, rows[2], "approve", "ev-b-approve-3")

        # Identity drift -> blocked (identity_source_hash mutated against ch-1..3).
        drift_spec = _make_spec(
            ids,
            version,
            spec_key="ds-4",
            chapter_number=4,
            stable_id="char-arya",
            entity_key="char-arya",
            identity_source_hash="e" * 64,
            style_profile=warm,
            divergence=branch_div,
            identity_divergence={"palette": "soft greys"},
        )
        blocked_row, _, _ = await _store_candidate(
            session, storage, ids, version, drift_spec, "ch-4", 4
        )

        # Declared style divergence -> needs_review (own identity char-brand).
        brand_specs = [
            _make_spec(
                ids,
                version,
                spec_key="ds-5",
                chapter_number=5,
                stable_id="char-brand",
                entity_key="char-brand",
                identity_source_hash=HEX64,
                style_profile=warm,
                divergence=branch_div,
                identity_divergence={"palette": "teal"},
            ),
            _make_spec(
                ids,
                version,
                spec_key="ds-6",
                chapter_number=6,
                stable_id="char-brand",
                entity_key="char-brand",
                identity_source_hash=HEX64,
                style_profile=cold,
                divergence=declared,
                identity_divergence={"palette": "teal"},
            ),
        ]
        needs_rows = []
        for index, spec in enumerate(brand_specs, start=5):
            row, _, _ = await _store_candidate(
                session, storage, ids, version, spec, f"ch-{index}", index
            )
            needs_rows.append(row)

        # Chapter 1 store body (frozen spec + candidate + bytes) so the browser
        # can replay a store request and prove a mutated source hash fails 409.
        ch1_spec = specs[0]
        ch1_payload = bytes([1]) * 8
        ch1_candidate = _candidate_write(
            ch1_spec,
            asset_key="ch-1",
            chapter_number=1,
            content_hash=_content_hash(ch1_payload),
        )

        await session.commit()

        return {
            "owner_id": ids["owner_id"],
            "novel_id": ids["novel_id"],
            "project_id": ids["project_id"],
            "fork_id": ids["fork_id"],
            "visual_version_id": version.id,
            "candidates": {
                "approved": [rows[0].asset_id, rows[2].asset_id],
                "rejected": [rows[1].asset_id],
                "blocked": [blocked_row.asset_id],
                "needs_review": [row.asset_id for row in needs_rows],
            },
            "approved_asset_ids": [rows[0].asset_id, rows[2].asset_id],
            "rejected_asset_ids": [rows[1].asset_id],
            "blocked_asset_ids": [blocked_row.asset_id],
            "needs_review_asset_ids": [row.asset_id for row in needs_rows],
            "approved_candidate_ids": [rows[0].id, rows[2].id],
            "rejected_candidate_id": rows[1].id,
            "blocked_candidate_id": blocked_row.id,
            "needs_review_candidate_ids": [row.id for row in needs_rows],
            "store_body": {
                "spec": ch1_spec.model_dump(mode="json"),
                "candidate": ch1_candidate.model_dump(mode="json"),
                "payload_base64": base64.b64encode(ch1_payload).decode(),
            },
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 38-04 derivative visual review seed")
    parser.add_argument("--e2e-seed-user")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.e2e_seed_user:
        import json

        print(
            "E2E_RESULT="
            + json.dumps(asyncio.run(seed_browser_review(args.e2e_seed_user)))
        )
        return 0
    parser.error("choose --e2e-seed-user")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
