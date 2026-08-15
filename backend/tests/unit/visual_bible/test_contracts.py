"""Phase 30-01 Visual Bible contract, version, evidence and permission tests.

Covers REQ-VIS-01 / D-30-01..D-30-04:
- strict typed contract rejects extra fields, out-of-range offsets, missing
  evidence on canon_fact, wrong content/manifest hashes and duplicate stable
  IDs;
- candidate versions carry parent/schema/policy/manifest lineage and stay
  candidate-only until an explicit append-only review action;
- evidence is source-snapshot-linked and spoiler-cutoff-gated;
- reference assets expose rights/provenance and can never be silently canon.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect

from app.models.visual_bible import (
    VisualBibleReviewEvent,
    VisualBibleVersion,
    VisualEntity,
    VisualReferenceAsset,
)
from app.schemas.visual_bible import (
    LEGAL_REVIEW_TRANSITIONS,
    REVIEW_ACTION_TO_STATE,
    VisualActorSource,
    VisualAuthority,
    VisualBibleGateError,
    VisualBibleVersionContract,
    VisualBibleVersionView,
    VisualClaimContract,
    VisualEntityContract,
    VisualEvidenceRef,
    VisualReferenceAssetContract,
    VisualReviewAction,
    VisualReviewEventInput,
    VisualReviewState,
    VisualRightsStatus,
    canonical_claim_payload,
    canonical_visual_hash,
    claim_content_hash,
    recompute_manifest_hash,
    validate_claim_evidence,
    validate_claim_hash,
    validate_evidence_against_source,
    validate_review_event,
    validate_version_contract,
    version_manifest_payload,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "versions"

VB_TABLES = {
    "visual_bible_versions",
    "visual_bible_entities",
    "visual_bible_claims",
    "visual_bible_evidence_refs",
    "visual_bible_reference_assets",
    "visual_bible_review_events",
}

# Canonical hash of the authority vocabulary so a future rename cannot pass
# silently (stable hash pins the four-label contract, D-30-02).
AUTHORITY_HASH = canonical_visual_hash(
    {
        "labels": [
            "canon_fact",
            "probable_inference",
            "literary_interpretation",
            "user_interpretation",
        ]
    }
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _evidence(**overrides):
    payload = {
        "evidence_key": "ev-1",
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": HEX64,
        "chapter_id": 3,
        "chapter_number": 3,
        "source_start": 0,
        "source_end": 12,
        "content_hash": HEX64_C,
        "excerpt": "golden hair",
        "cutoff_chapter": 8,
    }
    payload.update(overrides)
    return VisualEvidenceRef.model_validate(payload)


def _claim(**overrides):
    payload = {
        "claim_key": "char-arya-hair",
        "entity_stable_id": "char-arya",
        "authority": "canon_fact",
        "description": "dark brown hair, grey eyes",
        "author": None,
        "rationale": None,
        "cutoff_chapter": 8,
        "claim_hash": "0" * 64,
        "evidence_refs": [_evidence().model_dump()],
    }
    payload.update(overrides)
    claim = VisualClaimContract.model_validate(payload)
    if "claim_hash" not in overrides:
        claim = claim.model_copy(update={"claim_hash": claim_content_hash(claim)})
    return claim


def _entity(**overrides):
    payload = {
        "stable_id": "char-arya",
        "entity_key": "char-arya",
        "entity_type": "character",
        "description": "A girl with grey eyes and a bow.",
        "authority": "canon_fact",
        "disclosure_cutoff": 8,
    }
    payload.update(overrides)
    return VisualEntityContract.model_validate(payload)


def _asset(**overrides):
    payload = {
        "asset_key": "ref-arya-sketch",
        "asset_id": "obj-1",
        "mime_type": "image/png",
        "bytes_hash": HEX64_B,
        "rights_status": "unreviewed",
        "provenance": {"source": "user-upload", "license": "pending"},
    }
    payload.update(overrides)
    return VisualReferenceAssetContract.model_validate(payload)


def _version(**overrides):
    payload = {
        "schema_version": "visual-bible.v1",
        "artifact_kind": "visual_bible",
        "owner_id": 11,
        "novel_id": 22,
        "version_key": "vb-arya",
        "revision_number": 1,
        "parent_version_id": None,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": HEX64,
        "cutoff_chapter": 8,
        "schema_hash": HEX64,
        "policy_hash": HEX64_B,
        "prompt_hash": HEX64_C,
        "model_hash": None,
        "config_hash": None,
        "manifest_hash": "0" * 64,
        "style_profile": None,
        "constraints": None,
        "entities": [_entity().model_dump()],
        "claims": [_claim().model_dump()],
        "reference_assets": [_asset().model_dump()],
        "review_state": "candidate",
    }
    payload.update(overrides)
    version = VisualBibleVersionContract.model_validate(payload)
    if "manifest_hash" not in overrides:
        version = version.model_copy(
            update={"manifest_hash": recompute_manifest_hash(version)}
        )
    return version


def _review_event(**overrides):
    payload = {
        "owner_id": 11,
        "novel_id": 22,
        "version_id": 1,
        "action": "approve",
        "actor_source": "human",
        "actor": "reader",
        "reason": "matches the text",
        "event_key": "ev-approve-1",
        "from_review_state": "candidate",
    }
    payload.update(overrides)
    return VisualReviewEventInput.model_validate(payload)


# ---------------------------------------------------------------------------
# Authority vocabulary and strict schemas
# ---------------------------------------------------------------------------


def test_authority_vocabulary_is_exactly_four_labels():
    labels = [label.value for label in VisualAuthority]
    assert labels == [
        "canon_fact",
        "probable_inference",
        "literary_interpretation",
        "user_interpretation",
    ]
    assert (
        AUTHORITY_HASH
        == "578e80c59a8eaecc0e1beeb4f50a4de2c74aaa9a8ea7d881069bc009cfedbd3c"
    )


def test_review_action_and_state_vocabulary():
    assert [a.value for a in VisualReviewAction] == [
        "approve",
        "reject",
        "edit",
        "supersede",
        "needs_relink",
    ]
    assert [s.value for s in VisualReviewState] == [
        "candidate",
        "approved",
        "rejected",
        "superseded",
        "needs_relink",
    ]


def test_strict_schema_rejects_extra_fields():
    from app.schemas.visual_bible import VisualEvidenceRef as RefSchema

    with pytest.raises(ValidationError):
        RefSchema.model_validate(
            {
                "evidence_key": "ev-1",
                "source_snapshot_id": "ss-1",
                "source_snapshot_hash": HEX64,
                "chapter_id": 3,
                "chapter_number": 3,
                "source_start": 0,
                "source_end": 12,
                "content_hash": HEX64_C,
                "cutoff_chapter": 8,
                "canon": True,  # extra field must be rejected
            }
        )

    with pytest.raises(ValidationError):
        VisualBibleVersionContract.model_validate(
            _version().model_dump() | {"promote_to_canon": True}
        )


def test_reference_asset_write_contract_carries_no_approval_flag():
    """Generated assets cannot be marked approved at write time (D-30-01)."""
    with pytest.raises(ValidationError):
        VisualReferenceAssetContract.model_validate(
            _asset().model_dump() | {"approved": True}
        )


# ---------------------------------------------------------------------------
# Offsets, hashes and evidence gates
# ---------------------------------------------------------------------------


def test_evidence_rejects_inverted_or_out_of_range_offsets():
    with pytest.raises(ValidationError):
        _evidence(source_start=12, source_end=12)  # empty range
    with pytest.raises(ValidationError):
        _evidence(source_start=13, source_end=12)  # inverted
    with pytest.raises(ValidationError):
        _evidence(source_start=-1, source_end=5)  # negative


def test_evidence_rejects_chapter_beyond_spoiler_cutoff():
    with pytest.raises(ValidationError):
        _evidence(chapter_number=9, cutoff_chapter=8)
    ok = _evidence(chapter_number=8, cutoff_chapter=8)
    assert ok.chapter_number == ok.cutoff_chapter


def test_evidence_against_source_verifies_offsets_and_content_hash():
    source_text = "The captain wore a long grey coat."
    ref = _evidence(
        evidence_key="ev-coat",
        source_start=20,
        source_end=34,
        content_hash=canonical_visual_hash(
            {"slice": source_text[20:34]}
        ),  # wrong hash on purpose below
    )
    # Correct slice hash passes.
    import hashlib

    good = ref.model_copy(
        update={"content_hash": hashlib.sha256(source_text[20:34].encode()).hexdigest()}
    )
    validate_evidence_against_source(source_text, good)  # no raise

    with pytest.raises(VisualBibleGateError):
        validate_evidence_against_source(source_text, ref)  # hash mismatch

    bad_range = ref.model_copy(update={"source_end": 200})
    with pytest.raises(VisualBibleGateError):
        validate_evidence_against_source(source_text, bad_range)


def test_claim_hash_is_byte_replayable():
    claim = _claim()
    assert claim.claim_hash == claim_content_hash(claim)
    assert canonical_claim_payload(claim)["authority"] == "canon_fact"

    with pytest.raises(VisualBibleGateError):
        validate_claim_hash(claim.model_copy(update={"claim_hash": HEX64_B}))


def test_canon_fact_without_evidence_is_rejected():
    with pytest.raises(ValidationError):
        _claim(authority="canon_fact", evidence_refs=[])


def test_interpretation_claims_require_author_and_rationale():
    with pytest.raises(ValidationError):
        _claim(authority="user_interpretation", author=None, rationale=None)
    ok = _claim(
        authority="user_interpretation",
        author="reader",
        rationale="my reading of chapter 3",
        evidence_refs=[],
    )
    assert ok.authority is VisualAuthority.USER_INTERPRETATION


def test_claim_evidence_must_match_version_snapshot_and_cutoff():
    version = _version()
    claim = version.claims[0]

    validate_claim_evidence(
        claim,
        source_snapshot_id=version.source_snapshot_id,
        source_snapshot_hash=version.source_snapshot_hash,
        cutoff_chapter=version.cutoff_chapter,
    )

    with pytest.raises(VisualBibleGateError):
        validate_claim_evidence(
            claim,
            source_snapshot_id="other-ss",
            source_snapshot_hash=version.source_snapshot_hash,
            cutoff_chapter=version.cutoff_chapter,
        )
    with pytest.raises(VisualBibleGateError):
        validate_claim_evidence(
            claim,
            source_snapshot_id=version.source_snapshot_id,
            source_snapshot_hash=version.source_snapshot_hash,
            cutoff_chapter=3,  # evidence chapter 3 allowed; claim cutoff differs
        )


# ---------------------------------------------------------------------------
# Version lineage and cross-field gates
# ---------------------------------------------------------------------------


def test_candidate_version_carries_parent_hash_schema_policy_lineage():
    version = _version()
    assert version.schema_version == "visual-bible.v1"
    assert version.artifact_kind == "visual_bible"
    assert version.parent_version_id is None
    assert version.revision_number == 1
    assert version.manifest_hash == recompute_manifest_hash(version)
    assert version.schema_hash == HEX64
    assert version.policy_hash == HEX64_B
    assert version.source_snapshot_hash == HEX64
    assert version.review_state is VisualReviewState.CANDIDATE

    child = _version(revision_number=2, parent_version_id=1)
    assert child.parent_version_id == 1
    assert child.revision_number == 2
    assert child.manifest_hash == recompute_manifest_hash(child)


def test_version_manifest_payload_is_canonical_and_stable():
    a = _version()
    b = _version()
    assert version_manifest_payload(a) == version_manifest_payload(b)
    assert recompute_manifest_hash(a) == recompute_manifest_hash(b)
    # Changing a claim changes the manifest hash.
    changed = _version(claims=[_claim(description="raven-black hair").model_dump()])
    assert changed.manifest_hash != a.manifest_hash


def test_duplicate_stable_ids_are_rejected():
    with pytest.raises(VisualBibleGateError):
        validate_version_contract(
            _version(
                entities=[
                    _entity().model_dump(),
                    _entity(
                        stable_id="char-arya", entity_key="char-arya-2"
                    ).model_dump(),
                ]
            )
        )


def test_claim_referencing_unknown_entity_is_rejected():
    with pytest.raises(VisualBibleGateError):
        validate_version_contract(
            _version(claims=[_claim(entity_stable_id="char-unknown").model_dump()])
        )


def test_wrong_manifest_hash_is_rejected():
    with pytest.raises(VisualBibleGateError):
        validate_version_contract(_version(manifest_hash=HEX64_B))


def test_valid_version_passes_contract():
    validate_version_contract(_version())  # no raise


# ---------------------------------------------------------------------------
# Validation fixtures (30-VALIDATION.md): vb-basic-v1 / vb-interpretation-v1
# ---------------------------------------------------------------------------


def test_vb_basic_v1_covers_all_entity_types_with_snapshot_evidence():
    """vb-basic-v1: character/place/item/faction/style each carry canon claims
    with the same source snapshot's chapter/range/hash."""
    entities = [
        _entity(stable_id="char-arya", entity_key="char-arya", entity_type="character"),
        _entity(
            stable_id="place-rivendell",
            entity_key="place-rivendell",
            entity_type="place",
        ),
        _entity(stable_id="item-sting", entity_key="item-sting", entity_type="item"),
        _entity(
            stable_id="faction-rangers",
            entity_key="faction-rangers",
            entity_type="faction",
        ),
        _entity(stable_id="style-prose", entity_key="style-prose", entity_type="style"),
    ]
    claims = [
        _claim(
            claim_key="char-arya-hair",
            entity_stable_id="char-arya",
            evidence_refs=[_evidence().model_dump()],
        ),
        _claim(
            claim_key="place-rivendell-hall",
            entity_stable_id="place-rivendell",
            description="a wide stone hall with tall windows",
            evidence_refs=[_evidence().model_dump()],
        ),
        _claim(
            claim_key="item-sting-glint",
            entity_stable_id="item-sting",
            description="the blade gleams pale blue",
            evidence_refs=[_evidence().model_dump()],
        ),
        _claim(
            claim_key="faction-rangers-grey",
            entity_stable_id="faction-rangers",
            description="grey cloaks over worn mail",
            evidence_refs=[_evidence().model_dump()],
        ),
        _claim(
            claim_key="style-voice",
            entity_stable_id="style-prose",
            authority="literary_interpretation",
            description="sparse, weather-driven prose",
            author="critic",
            rationale="consistent chapter pacing",
            evidence_refs=[],
        ),
    ]
    basic = _version(
        entities=[entity.model_dump() for entity in entities],
        claims=[claim.model_dump() for claim in claims],
    )
    validate_version_contract(basic)
    assert len(basic.entities) == 5
    assert {entity.entity_type for entity in basic.entities} == {
        "character",
        "place",
        "item",
        "faction",
        "style",
    }
    for claim in basic.claims:
        for ref in claim.evidence_refs:
            assert ref.source_snapshot_id == basic.source_snapshot_id
            assert ref.cutoff_chapter == basic.cutoff_chapter


def test_vb_interpretation_v1_keeps_authority_labels_distinct():
    """vb-interpretation-v1: the same entity can carry probable_inference and
    user_interpretation claims; the contract never collapses authority."""
    entity = _entity()
    claims = [
        _claim(
            claim_key="char-arya-cloak",
            authority="probable_inference",
            description="wears a travelling cloak",
            author="machine",
            rationale="cloak is implied by weather talk",
            evidence_refs=[],
        ),
        _claim(
            claim_key="char-arya-mood",
            authority="user_interpretation",
            description="reads as quietly melancholic",
            author="reader",
            rationale="her silences in chapter 3",
            evidence_refs=[],
        ),
    ]
    version = _version(
        entities=[entity.model_dump()],
        claims=[claim.model_dump() for claim in claims],
    )
    validate_version_contract(version)

    view = VisualBibleVersionView.model_validate(
        {
            "id": 1,
            "owner_id": version.owner_id,
            "novel_id": version.novel_id,
            "version_key": version.version_key,
            "revision_number": version.revision_number,
            "source_snapshot_id": version.source_snapshot_id,
            "source_snapshot_hash": version.source_snapshot_hash,
            "cutoff_chapter": version.cutoff_chapter,
            "schema_version": version.schema_version,
            "schema_hash": version.schema_hash,
            "policy_hash": version.policy_hash,
            "manifest_hash": version.manifest_hash,
            "review_state": version.review_state.value,
            "entities": [
                {
                    "stable_id": entity.stable_id,
                    "entity_key": entity.entity_key,
                    "entity_type": entity.entity_type.value,
                    "description": entity.description,
                    "authority": entity.authority.value,
                    "disclosure_cutoff": entity.disclosure_cutoff,
                    "claims": [
                        {
                            "claim_key": claim.claim_key,
                            "entity_stable_id": claim.entity_stable_id,
                            "authority": claim.authority.value,
                            "description": claim.description,
                            "author": claim.author,
                            "rationale": claim.rationale,
                            "cutoff_chapter": claim.cutoff_chapter,
                            "claim_hash": claim.claim_hash,
                            "evidence_refs": [],
                        }
                        for claim in claims
                    ],
                }
            ],
        }
    )
    labels = {claim.authority for claim in view.entities[0].claims}
    assert labels == {
        VisualAuthority.PROBABLE_INFERENCE,
        VisualAuthority.USER_INTERPRETATION,
    }
    assert len(labels) == 2  # never collapsed into a single label


# ---------------------------------------------------------------------------
# Review gates (append-only, explicit, idempotent)
# ---------------------------------------------------------------------------


def test_review_legal_transition_map_is_closed():
    # Every state in the vocabulary has an explicit legal-transition entry.
    assert set(LEGAL_REVIEW_TRANSITIONS) == set(VisualReviewState)
    # Every allowed transition has a well-defined result state.
    for actions in LEGAL_REVIEW_TRANSITIONS.values():
        for action in actions:
            assert action in REVIEW_ACTION_TO_STATE
            assert _state_after(_state_for_action(action), action) is not None


def _state_for_action(action: VisualReviewAction) -> VisualReviewState:
    """Pick a state from which the given action is legal (for the map test)."""
    for state, actions in LEGAL_REVIEW_TRANSITIONS.items():
        if action in actions:
            return state
    return VisualReviewState.SUPERSEDED


def _state_after(state, action):
    from app.schemas.visual_bible import review_state_after

    try:
        return review_state_after(state, action)
    except VisualBibleGateError:
        return None


def test_review_approve_reject_supersede_chain():
    # candidate -> approved
    assert _state_after("candidate", "approve") is VisualReviewState.APPROVED
    # approved cannot be approved again; must be superseded or needs_relink.
    assert _state_after("approved", "approve") is None
    assert _state_after("approved", "supersede") is VisualReviewState.SUPERSEDED
    # rejected can be edited (new child) or superseded.
    assert _state_after("candidate", "reject") is VisualReviewState.REJECTED
    assert _state_after("rejected", "edit") is VisualReviewState.REJECTED
    # needs_relink resolution loop.
    assert _state_after("approved", "needs_relink") is VisualReviewState.NEEDS_RELINK
    assert _state_after("needs_relink", "approve") is VisualReviewState.APPROVED
    # terminal state.
    assert _state_after("superseded", "approve") is None


def test_review_event_derives_result_state_and_rejects_duplicate_keys():
    result = validate_review_event(_review_event())
    assert result is VisualReviewState.APPROVED

    with pytest.raises(VisualBibleGateError):
        validate_review_event(
            _review_event(),
            seen_event_keys={"ev-approve-1"},  # idempotency replay
        )

    with pytest.raises(VisualBibleGateError):
        validate_review_event(
            _review_event(
                action="approve",
                from_review_state="approved",
                event_key="ev-approve-2",
            )
        )


def test_machine_review_action_is_explicit():
    event = _review_event(actor_source="machine", event_key="ev-machine-1")
    assert event.actor_source is VisualActorSource.MACHINE
    assert validate_review_event(event) is VisualReviewState.APPROVED


# ---------------------------------------------------------------------------
# Reference assets: rights/provenance, no silent canon
# ---------------------------------------------------------------------------


def test_reference_asset_rights_status_defaults_and_provenance():
    asset = _asset()
    assert asset.rights_status is VisualRightsStatus.UNREVIEWED
    assert asset.provenance["license"] == "pending"
    with pytest.raises(ValidationError):
        _asset(rights_status="public_domain")  # not a closed value


def test_approval_never_touches_asset_approved_flag():
    """Approve is a review-state transition; generated assets stay non-canon."""
    version = _version()
    validate_version_contract(version)
    assert version.reference_assets[0].rights_status is VisualRightsStatus.UNREVIEWED

    view = VisualBibleVersionView.model_validate(
        {
            "id": 1,
            "owner_id": version.owner_id,
            "novel_id": version.novel_id,
            "version_key": version.version_key,
            "revision_number": version.revision_number,
            "source_snapshot_id": version.source_snapshot_id,
            "source_snapshot_hash": version.source_snapshot_hash,
            "cutoff_chapter": version.cutoff_chapter,
            "schema_version": version.schema_version,
            "schema_hash": version.schema_hash,
            "policy_hash": version.policy_hash,
            "manifest_hash": version.manifest_hash,
            "review_state": "candidate",
            "reference_assets": [
                {
                    "asset_key": asset.asset_key,
                    "asset_id": asset.asset_id,
                    "mime_type": asset.mime_type,
                    "bytes_hash": asset.bytes_hash,
                    "rights_status": asset.rights_status.value,
                    "approved": False,
                }
                for asset in version.reference_assets
            ],
        }
    )
    # Read envelope keeps the asset visible but not approved.
    assert view.reference_assets[0].approved is False
    assert view.review_state is VisualReviewState.CANDIDATE


# ---------------------------------------------------------------------------
# ORM metadata and migration chain
# ---------------------------------------------------------------------------


def test_visual_bible_tables_are_registered_on_metadata():
    tables = set(VisualBibleVersion.metadata.tables)
    assert VB_TABLES <= tables


def test_orm_exports_all_visual_bible_entities():
    from app.models import (
        VisualBibleReviewEvent as ExportedReviewEvent,
        VisualBibleVersion as ExportedVersion,
        VisualClaim as ExportedClaim,
        VisualEntity as ExportedEntity,
        VisualEvidenceRef as ExportedEvidence,
        VisualReferenceAsset as ExportedAsset,
    )

    assert ExportedVersion.__tablename__ == "visual_bible_versions"
    assert ExportedEntity.__tablename__ == "visual_bible_entities"
    assert ExportedClaim.__tablename__ == "visual_bible_claims"
    assert ExportedEvidence.__tablename__ == "visual_bible_evidence_refs"
    assert ExportedAsset.__tablename__ == "visual_bible_reference_assets"
    assert ExportedReviewEvent.__tablename__ == "visual_bible_review_events"


def test_version_orm_has_owner_novel_parent_and_hash_lineage():
    cols = set(inspect(VisualBibleVersion).columns.keys())
    assert {
        "owner_id",
        "novel_id",
        "version_key",
        "revision_number",
        "parent_version_id",
        "source_snapshot_id",
        "source_snapshot_hash",
        "cutoff_chapter",
        "review_state",
        "schema_version",
        "schema_hash",
        "policy_hash",
        "prompt_hash",
        "model_hash",
        "config_hash",
        "manifest_hash",
    } <= cols

    unique = {
        tuple(c.name for c in u.columns)
        for u in VisualBibleVersion.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "version_key") in unique
    assert ("owner_id", "novel_id", "id") in unique

    check_names = {
        c.name for c in VisualBibleVersion.__table__.constraints if hasattr(c, "name")
    }
    assert "ck_visual_bible_versions_review_state" in check_names


def test_entity_stable_id_is_reusable_and_scoped():
    unique = {
        tuple(c.name for c in u.columns)
        for u in VisualEntity.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "version_id", "stable_id") in unique
    cols = set(inspect(VisualEntity).columns.keys())
    assert {"stable_id", "entity_type", "disclosure_cutoff", "authority"} <= cols


def test_evidence_orm_enforces_spoiler_cutoff_check():
    from app.models.visual_bible import VisualEvidenceRef as EvidenceRefModel

    check_names = {
        c.name for c in EvidenceRefModel.__table__.constraints if hasattr(c, "name")
    }
    assert "ck_visual_bible_evidence_spoiler_cutoff" in check_names
    assert "ck_visual_bible_evidence_offsets" in check_names
    assert EvidenceRefModel.__table__.c.content_hash.type.length == 64


def test_review_event_orm_is_idempotent_and_append_only():
    unique = {
        tuple(c.name for c in u.columns)
        for u in VisualBibleReviewEvent.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "version_id", "event_key") in unique
    check_names = {
        c.name
        for c in VisualBibleReviewEvent.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_visual_bible_review_events_action" in check_names


def test_reference_asset_orm_has_rights_and_approval_gate():
    cols = set(inspect(VisualReferenceAsset).columns.keys())
    assert {
        "asset_key",
        "asset_id",
        "mime_type",
        "bytes_hash",
        "rights_status",
        "provenance",
        "approved",
    } <= cols
    check_names = {
        c.name for c in VisualReferenceAsset.__table__.constraints if hasattr(c, "name")
    }
    assert "ck_visual_bible_assets_rights_status" in check_names
    assert VisualReferenceAsset.__table__.c.approved.server_default.arg == "false"


def _load_migration(filename: str):
    path = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chain_is_serial_on_top_of_2801_head():
    migration = _load_migration("20260801_visual_bible.py")
    assert migration.revision == "20260801_visual_bible"
    assert migration.down_revision == "20260801_2801"
    # ORM vocabulary must match the migration CHECK expressions.
    assert "visual_bible_versions" in migration.__doc__
    for label in ("canon_fact", "user_interpretation"):
        assert f"'{label}'" in migration._AUTHORITY_LABELS
    assert "'needs_relink'" in migration._REVIEW_ACTIONS


def test_no_cover_or_active_pointer_crossing_in_contract():
    """Visual Bible never reuses cover_url and has no active pointer (D-30-01)."""
    from app.schemas.visual_bible import VisualBibleVersionContract as VB

    fields = set(VB.model_fields)
    assert "cover_url" not in fields
    assert "active_pointer" not in fields
    assert "current_revision" not in fields
    assert "canon_url" not in fields
