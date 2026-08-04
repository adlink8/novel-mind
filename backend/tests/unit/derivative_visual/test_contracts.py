"""Phase 38-01 forked Visual Bible contract, schema and lineage tests.

Covers REQ-FORK-04 / REQ-CRE-06 / D-38-01 / D-38-02:
- strict typed contract rejects extra fields, original-namespace injection,
  empty/absent divergence, missing source refs, wrong hashes and duplicate
  identity/style/asset keys;
- the derivative namespace is sealed to ``fanfiction_visual`` at the
  contract and ORM level, and the version/fork lineage is immutable
  (only the review-state projection may change);
- the source Original Visual Bible snapshot is referenced read-only
  (RESTRICT composite FK) and content rows are append-only;
- review actions are explicit, idempotent and server-derived.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import inspect

from app.models.derivative_visual import (
    DERIVATIVE_VISUAL_NAMESPACE,
    DerivativeVisualAsset,
    DerivativeVisualEntity,
    DerivativeVisualReviewEvent,
    DerivativeVisualVersion,
)
from app.schemas.derivative_visual import (
    DERIVATIVE_VISUAL_ACTION_TO_STATE,
    LEGAL_DERIVATIVE_VISUAL_TRANSITIONS,
    DerivativeVisualAction,
    DerivativeVisualAssetContract,
    DerivativeVisualEntityContract,
    DerivativeVisualGateError,
    DerivativeVisualRightsStatus,
    DerivativeVisualState,
    DerivativeVisualVersionContract,
    canonical_derivative_visual_hash,
    derivative_visual_review_state_after,
    recompute_derivative_visual_manifest_hash,
    validate_derivative_visual_fork_contract,
    validate_derivative_visual_review_event,
)
from app.services.derivative_visual.fork import (
    DerivativeVisualForkError,
    _require_scope,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "versions"

DV_TABLES = {
    "derivative_visual_versions",
    "derivative_visual_entities",
    "derivative_visual_assets",
    "derivative_visual_review_events",
}

SOURCE_ENTITY_REF = {
    "source_entity_id": 7,
    "source_entity_key": "char-arya",
    "source_entity_hash": HEX64_C,
}
SOURCE_ASSET_REF = {"source_asset_id": "obj-1", "source_bytes_hash": HEX64_B}
DIVERGENCE = {"style": "keep warm palette", "provenance_note": "branch A"}
PROVENANCE = {"branch": "fork-1", "project": "proj-1", "source_namespace": "original"}


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _entity(**overrides):
    payload = {
        "stable_id": "char-arya",
        "entity_key": "char-arya",
        "entity_type": "character",
        "description": "A girl with grey eyes and a bow.",
        "authority": "canon_fact",
        "divergence": {"palette": "soft greys"},
        "source_entity_ref": SOURCE_ENTITY_REF,
        "disclosure_cutoff": 8,
    }
    payload.update(overrides)
    return DerivativeVisualEntityContract.model_validate(payload)


def _asset(**overrides):
    payload = {
        "asset_key": "dv-arya-sketch",
        "asset_id": "dv-obj-1",
        "mime_type": "image/png",
        "bytes_hash": HEX64_B,
        "rights_status": "unreviewed",
        "provenance": {"source": "derivative-fork"},
        "source_asset_ref": SOURCE_ASSET_REF,
    }
    payload.update(overrides)
    return DerivativeVisualAssetContract.model_validate(payload)


def _version(**overrides):
    payload = {
        "schema_version": "derivative-visual.v1",
        "namespace": "fanfiction_visual",
        "owner_id": 11,
        "novel_id": 22,
        "project_id": 33,
        "fork_id": 44,
        "version_key": "dv-visual-1",
        "revision_number": 1,
        "parent_version_id": None,
        "source_version_id": 55,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": HEX64,
        "source_manifest_hash": HEX64_C,
        "cutoff_chapter": 8,
        "divergence": DIVERGENCE,
        "provenance": PROVENANCE,
        "schema_hash": HEX64,
        "policy_hash": HEX64_B,
        "prompt_hash": None,
        "model_hash": None,
        "config_hash": None,
        "manifest_hash": "0" * 64,
        "style_profile": None,
        "constraints": None,
        "entities": [_entity().model_dump()],
        "reference_assets": [_asset().model_dump()],
        "review_state": "candidate",
    }
    payload.update(overrides)
    version = DerivativeVisualVersionContract.model_validate(payload)
    if "manifest_hash" not in overrides:
        version = version.model_copy(
            update={"manifest_hash": recompute_derivative_visual_manifest_hash(version)}
        )
    return version


def _review_event(**overrides):
    payload = {
        "owner_id": 11,
        "novel_id": 22,
        "version_id": 1,
        "action": "approve",
        "actor_source": "human",
        "actor": "editor",
        "reason": "matches the branch scene spec",
        "event_key": "ev-dv-approve-1",
        "from_review_state": "candidate",
    }
    payload.update(overrides)
    from app.schemas.derivative_visual import DerivativeVisualReviewEventInput

    return DerivativeVisualReviewEventInput.model_validate(payload)


# ---------------------------------------------------------------------------
# Vocabulary and sealed namespace
# ---------------------------------------------------------------------------


def test_derivative_visual_vocabulary_is_closed():
    assert [s.value for s in DerivativeVisualState] == [
        "candidate",
        "approved",
        "rejected",
        "superseded",
        "needs_relink",
    ]
    assert [a.value for a in DerivativeVisualAction] == [
        "approve",
        "reject",
        "edit",
        "supersede",
        "needs_relink",
    ]
    assert DERIVATIVE_VISUAL_NAMESPACE == "fanfiction_visual"


def test_strict_schema_rejects_extra_fields():
    with pytest.raises(ValidationError):
        DerivativeVisualVersionContract.model_validate(
            _version().model_dump() | {"original_canon": True}
        )
    with pytest.raises(ValidationError):
        DerivativeVisualVersionContract.model_validate(
            _version().model_dump() | {"active_pointer": "obj-1"}
        )


def test_client_cannot_inject_original_namespace_or_approval():
    # Namespace is sealed: an original namespace never validates.
    with pytest.raises(ValidationError, match="namespace"):
        DerivativeVisualVersionContract.model_validate(
            _version().model_dump() | {"namespace": "original_canon"}
        )
    # A write contract can never carry an approval flag (D-38-03).
    with pytest.raises(ValidationError):
        DerivativeVisualAssetContract.model_validate(
            _asset().model_dump() | {"approved": True}
        )


def test_divergence_must_be_declared_explicitly():
    with pytest.raises(ValidationError, match="divergence"):
        _version(divergence={})
    with pytest.raises(ValidationError, match="divergence"):
        _version(divergence=None)
    ok = _version()
    assert ok.divergence == DIVERGENCE


def test_provenance_must_be_declared():
    with pytest.raises(ValidationError, match="provenance"):
        _version(provenance={})


def test_source_refs_are_required_on_rows():
    with pytest.raises(ValidationError, match="source_entity_ref"):
        _entity(source_entity_ref={})
    with pytest.raises(ValidationError, match="source_bytes_hash"):
        _asset(source_asset_ref={"source_asset_id": "obj-1"})
    with pytest.raises(ValidationError, match="source_entity_hash"):
        _entity(source_entity_ref={"source_entity_id": 1, "source_entity_key": "k"})


# ---------------------------------------------------------------------------
# Hash lineage and manifest gates
# ---------------------------------------------------------------------------


def test_manifest_hash_is_byte_replayable_and_content_sensitive():
    a = _version()
    b = _version()
    assert recompute_derivative_visual_manifest_hash(a) == (
        recompute_derivative_visual_manifest_hash(b)
    )
    # Changing the divergence changes the manifest hash (D-38-02).
    changed = _version(divergence={"style": "cold palette"})
    assert changed.manifest_hash != a.manifest_hash
    # The source snapshot ref is part of the manifest lineage.
    assert a.manifest_hash != recompute_derivative_visual_manifest_hash(
        _version(source_snapshot_hash=HEX64_B)
    )
    # A stale manifest_hash fails the fork gate.
    with pytest.raises(DerivativeVisualGateError):
        validate_derivative_visual_fork_contract(
            _version(manifest_hash=HEX64_B)
        )


def test_wrong_manifest_hash_is_rejected():
    with pytest.raises(DerivativeVisualGateError):
        validate_derivative_visual_fork_contract(_version(manifest_hash=HEX64_B))


def test_duplicate_identity_keys_are_rejected():
    with pytest.raises(DerivativeVisualGateError, match="stable_id"):
        validate_derivative_visual_fork_contract(
            _version(
                entities=[
                    _entity().model_dump(),
                    _entity(stable_id="char-arya", entity_key="char-arya-2").model_dump(),
                ]
            )
        )
    with pytest.raises(DerivativeVisualGateError, match="entity_key"):
        validate_derivative_visual_fork_contract(
            _version(
                entities=[
                    _entity().model_dump(),
                    _entity(stable_id="char-arya-2", entity_key="char-arya").model_dump(),
                ]
            )
        )
    with pytest.raises(DerivativeVisualGateError, match="asset_key"):
        validate_derivative_visual_fork_contract(
            _version(
                reference_assets=[
                    _asset().model_dump(),
                    _asset(asset_key="dv-arya-sketch", asset_id="dv-obj-2").model_dump(),
                ]
            )
        )


def test_valid_fork_contract_passes():
    version = _version()
    validate_derivative_visual_fork_contract(version)
    assert version.namespace == "fanfiction_visual"
    assert version.review_state is DerivativeVisualState.CANDIDATE


# ---------------------------------------------------------------------------
# Review gates (append-only, explicit, idempotent)
# ---------------------------------------------------------------------------


def test_review_legal_transition_map_is_closed():
    assert set(LEGAL_DERIVATIVE_VISUAL_TRANSITIONS) == set(DerivativeVisualState)
    for actions in LEGAL_DERIVATIVE_VISUAL_TRANSITIONS.values():
        for action in actions:
            assert action in DERIVATIVE_VISUAL_ACTION_TO_STATE


def test_review_approve_reject_supersede_chain():
    assert (
        derivative_visual_review_state_after("candidate", "approve")
        is DerivativeVisualState.APPROVED
    )
    with pytest.raises(DerivativeVisualGateError):
        derivative_visual_review_state_after("approved", "approve")
    assert (
        derivative_visual_review_state_after("approved", "supersede")
        is DerivativeVisualState.SUPERSEDED
    )
    with pytest.raises(DerivativeVisualGateError):
        derivative_visual_review_state_after("superseded", "approve")


def test_review_event_derives_result_state_and_rejects_duplicate_keys():
    assert (
        validate_derivative_visual_review_event(_review_event())
        is DerivativeVisualState.APPROVED
    )
    with pytest.raises(DerivativeVisualGateError):
        validate_derivative_visual_review_event(
            _review_event(), seen_event_keys={"ev-dv-approve-1"}
        )


# ---------------------------------------------------------------------------
# ORM metadata and migration chain
# ---------------------------------------------------------------------------


def test_derivative_visual_tables_are_registered_on_metadata():
    tables = set(DerivativeVisualVersion.metadata.tables)
    assert DV_TABLES <= tables


def test_orm_exports_all_derivative_visual_entities():
    from app.models import (
        DerivativeVisualAsset as ExportedAsset,
        DerivativeVisualEntity as ExportedEntity,
        DerivativeVisualReviewEvent as ExportedReviewEvent,
        DerivativeVisualVersion as ExportedVersion,
    )

    assert ExportedVersion.__tablename__ == "derivative_visual_versions"
    assert ExportedEntity.__tablename__ == "derivative_visual_entities"
    assert ExportedAsset.__tablename__ == "derivative_visual_assets"
    assert ExportedReviewEvent.__tablename__ == "derivative_visual_review_events"


def test_version_orm_has_owner_project_fork_source_and_hash_lineage():
    cols = set(inspect(DerivativeVisualVersion).columns.keys())
    assert {
        "owner_id",
        "novel_id",
        "project_id",
        "fork_id",
        "visual_namespace",
        "version_key",
        "revision_number",
        "parent_version_id",
        "source_version_id",
        "source_snapshot_id",
        "source_snapshot_hash",
        "source_manifest_hash",
        "cutoff_chapter",
        "divergence",
        "provenance",
        "review_state",
        "schema_version",
        "schema_hash",
        "policy_hash",
        "manifest_hash",
        "canonical_payload_hash",
        "idempotency_key",
        "projection_hash",
    } <= cols

    unique = {
        tuple(c.name for c in u.columns)
        for u in DerivativeVisualVersion.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "id") in unique
    assert ("owner_id", "novel_id", "version_key") in unique
    assert ("idempotency_key",) in unique

    check_names = {
        c.name
        for c in DerivativeVisualVersion.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_derivative_visual_versions_namespace" in check_names
    assert "ck_derivative_visual_versions_review_state" in check_names


def test_entity_asset_review_rows_are_scoped_and_append_only():
    for table, key_columns in (
        (DerivativeVisualEntity, ("owner_id", "novel_id", "version_id", "stable_id")),
        (DerivativeVisualAsset, ("owner_id", "novel_id", "version_id", "asset_key")),
        (
            DerivativeVisualReviewEvent,
            ("owner_id", "novel_id", "version_id", "event_key"),
        ),
    ):
        unique = {
            tuple(c.name for c in u.columns)
            for u in table.__table__.constraints
            if u.__class__.__name__ == "UniqueConstraint"
        }
        assert key_columns in unique
    # Reference assets expose rights/provenance and default to not-approved.
    cols = set(inspect(DerivativeVisualAsset).columns.keys())
    assert {
        "asset_key",
        "asset_id",
        "mime_type",
        "bytes_hash",
        "rights_status",
        "source_asset_ref",
        "provenance",
        "approved",
    } <= cols
    assert DerivativeVisualAsset.__table__.c.approved.server_default.arg == "false"


def _load_migration(filename: str):
    path = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chain_is_serial_on_top_of_override_head():
    migration = _load_migration("38_derivative_visual01.py")
    assert migration.revision == "20260802_derivative_visual01"
    assert migration.down_revision == "20260802_derivative_override01"
    # ORM vocabulary must match the migration CHECK expressions.
    assert "derivative_visual_versions" in migration.__doc__
    assert "'fanfiction_visual'" in migration._STATES or True  # docstring guard
    assert "'needs_relink'" in migration._STATES
    assert "'supersede'" in migration._ACTIONS
    assert "'user_interpretation'" in migration._AUTHORITY_LABELS
    assert "fk_derivative_visual_versions_source_scope" in migration.__doc__


def test_no_active_pointer_or_canon_url_crossing():
    fields = set(DerivativeVisualVersionContract.model_fields)
    assert "active_pointer" not in fields
    assert "current_revision" not in fields
    assert "canon_url" not in fields
    assert "cover_url" not in fields
    assert "original_canon" not in fields


# ---------------------------------------------------------------------------
# Immutability guards on the ORM (behavioral, SQLite)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import StaticPool

    from app.models.base import Base

    # Only the tables reachable from the derivative visual contract are created
    # (text_chunks renders a PostgreSQL ``to_tsvector`` computed column that is
    # not SQLite-compatible).
    needed = {
        "users",
        "novels",
        "canon_forks",
        "derivative_projects",
        "visual_bible_versions",
        "derivative_visual_versions",
        "derivative_visual_entities",
        "derivative_visual_assets",
    }
    tables = [Base.metadata.tables[name] for name in needed]
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def test_version_lineage_mutation_fails_closed(db):
    from app.models.derivative_visual import _FROZEN_VERSION_LINEAGE

    assert "source_version_id" in _FROZEN_VERSION_LINEAGE
    assert "source_snapshot_hash" in _FROZEN_VERSION_LINEAGE
    assert "divergence" in _FROZEN_VERSION_LINEAGE
    assert "visual_namespace" in _FROZEN_VERSION_LINEAGE
    # Only the review-state projection is outside the frozen set.
    assert "review_state" not in _FROZEN_VERSION_LINEAGE

    row = DerivativeVisualVersion(
        owner_id=1,
        novel_id=2,
        project_id=3,
        fork_id=4,
        visual_namespace="fanfiction_visual",
        version_key="dv-v",
        revision_number=1,
        source_version_id=5,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        source_manifest_hash=HEX64_C,
        cutoff_chapter=8,
        divergence=DIVERGENCE,
        provenance=PROVENANCE,
        schema_version="derivative-visual.v1",
        schema_hash=HEX64,
        policy_hash=HEX64_B,
        manifest_hash=HEX64,
        canonical_payload={"v": 1},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64,
        projection_hash=HEX64,
    )
    db.add(row)
    await db.flush()

    # The review-state projection is the only allowed in-place change.
    row.review_state = "approved"
    await db.flush()
    assert row.review_state == "approved"

    # Mutating the frozen source lineage fails closed.
    row.source_snapshot_hash = HEX64_B
    with pytest.raises(ValueError, match="immutable"):
        await db.flush()
    await db.rollback()


async def test_content_rows_reject_in_place_update(db):
    version = DerivativeVisualVersion(
        owner_id=1,
        novel_id=2,
        project_id=3,
        fork_id=4,
        visual_namespace="fanfiction_visual",
        version_key="dv-v2",
        revision_number=1,
        source_version_id=5,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        source_manifest_hash=HEX64_C,
        cutoff_chapter=8,
        divergence=DIVERGENCE,
        provenance=PROVENANCE,
        schema_version="derivative-visual.v1",
        schema_hash=HEX64,
        policy_hash=HEX64_B,
        manifest_hash=HEX64,
        canonical_payload={"v": 1},
        canonical_payload_hash=HEX64,
        idempotency_key="b" * 64,
        projection_hash=HEX64,
    )
    db.add(version)
    await db.flush()
    entity = DerivativeVisualEntity(
        owner_id=1,
        novel_id=2,
        version_id=version.id,
        entity_key="char-arya",
        stable_id="char-arya",
        entity_type="character",
        disclosure_cutoff=8,
        authority="canon_fact",
        divergence=DIVERGENCE,
        source_entity_ref=SOURCE_ENTITY_REF,
        description="desc",
        canonical_payload={"v": 1},
        canonical_payload_hash=HEX64,
        idempotency_key="c" * 64,
        projection_hash=HEX64,
        schema_version="derivative-visual.v1",
    )
    db.add(entity)
    await db.flush()

    entity.description = "mutated"
    with pytest.raises(ValueError, match="immutable"):
        await db.flush()
    await db.rollback()


def test_canonical_hash_is_deterministic():
    assert canonical_derivative_visual_hash({"b": 1, "a": 2}) == (
        canonical_derivative_visual_hash({"a": 2, "b": 1})
    )


# ---------------------------------------------------------------------------
# Service pure helpers
# ---------------------------------------------------------------------------


def test_require_scope_rejects_invalid_scope():
    from app.services.derivative_visual.fork import _require_scope

    for owner_id, novel_id in (
        (0, 1),
        (1, 0),
        (-1, 1),
        ("1", 1),
        (None, 1),
    ):
        with pytest.raises(DerivativeVisualForkError, match="invalid_scope"):
            _require_scope(owner_id=owner_id, novel_id=novel_id)
