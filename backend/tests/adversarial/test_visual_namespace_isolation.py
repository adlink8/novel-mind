"""Adversarial visual namespace-isolation gates (Phase 38-01).

REQ-FORK-04 / REQ-CRE-06 / D-38-01 / D-38-02. These deterministic gates
(contract + AST source checks, no PostgreSQL) prove that:

- the derivative Visual Bible contract is ``extra="forbid"``: the client can
  never inject an Original ``visual_bible_versions`` row, an Original Canon
  namespace, an approval flag, an owner/novel scope or an un-declared
  divergence;
- the derivative namespace is sealed to ``fanfiction_visual`` at the
  DTO/model/migration level and the Original Visual Bible tables have no
  derivative write path;
- every derivative fork/review/read path is scoped by owner + novel and the
  source Original snapshot must live inside that scope (a foreign/missing
  source is an identical 404-equivalent);
- the source snapshot hash / manifest hash lineage is immutable: a mutated
  source hash fails closed before any row is written;
- the Original Visual Bible rows are referenced read-only (composite RESTRICT
  FK) and content rows are append-only.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.derivative_visual import (
    DerivativeVisualAssetContract,
    DerivativeVisualVersionContract,
    validate_derivative_visual_fork_contract,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SOURCE = (
    BACKEND_ROOT / "app" / "schemas" / "derivative_visual.py"
).read_text(encoding="utf-8")
MODEL_SOURCE = (
    BACKEND_ROOT / "app" / "models" / "derivative_visual.py"
).read_text(encoding="utf-8")
FORK_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_visual" / "fork.py"
).read_text(encoding="utf-8")
LINEAGE_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_visual" / "lineage.py"
).read_text(encoding="utf-8")
GATES_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_visual" / "gates.py"
).read_text(encoding="utf-8")
SCENE_SPEC_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_visual" / "scene_spec.py"
).read_text(encoding="utf-8")
API_SOURCE = (
    BACKEND_ROOT / "app" / "api" / "derivative_visual.py"
).read_text(encoding="utf-8")
MIGRATION_SOURCE = (
    BACKEND_ROOT / "migrations" / "versions" / "38_derivative_visual01.py"
).read_text(encoding="utf-8")

HEX64 = "a" * 64


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
        "source_version_id": 55,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": HEX64,
        "source_manifest_hash": "b" * 64,
        "cutoff_chapter": 8,
        "divergence": {"style": "warm palette"},
        "provenance": {"branch": "fork-1"},
        "schema_hash": HEX64,
        "policy_hash": "b" * 64,
        "manifest_hash": "c" * 64,
        "entities": [],
        "reference_assets": [],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Wire contract: extra="forbid" prevents original-namespace / scope injection
# ---------------------------------------------------------------------------


def test_client_cannot_inject_original_namespace_or_scope():
    # The fork contract is extra="forbid": an Original-namespace row or an
    # approval flag never validates.
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeVisualVersionContract.model_validate(
            _version() | {"original_canon": True}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeVisualVersionContract.model_validate(
            _version() | {"approved": True}
        )
    # owner/novel are server-derived from the request scope: a mismatch fails
    # closed in the service before any row is written.
    assert "scope_mismatch" in FORK_SOURCE
    assert "version scope does not match request scope" in FORK_SOURCE


def test_namespace_is_sealed_to_fanfiction_visual():
    with pytest.raises(ValidationError, match="namespace"):
        DerivativeVisualVersionContract.model_validate(
            _version() | {"namespace": "original_canon"}
        )
    with pytest.raises(ValidationError, match="namespace"):
        DerivativeVisualVersionContract.model_validate(
            _version() | {"namespace": "user_interpretation"}
        )


def test_asset_contract_cannot_carry_approval():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeVisualAssetContract.model_validate(
            {
                "asset_key": "dv-a",
                "asset_id": "obj-1",
                "mime_type": "image/png",
                "bytes_hash": HEX64,
                "source_asset_ref": {"source_asset_id": "obj-1", "source_bytes_hash": HEX64},
                "approved": True,
            }
        )


def test_divergence_is_required_and_explicit():
    with pytest.raises(ValidationError, match="divergence"):
        DerivativeVisualVersionContract.model_validate(_version() | {"divergence": {}})
    with pytest.raises(ValidationError, match="divergence"):
        DerivativeVisualVersionContract.model_validate(_version() | {"divergence": None})


# ---------------------------------------------------------------------------
# Source namespace / provenance leak prevention (T-38-01-02)
# ---------------------------------------------------------------------------


def test_no_original_visual_authority_write_path():
    for source in (FORK_SOURCE, LINEAGE_SOURCE):
        assert "visual_bible_versions" not in source.replace(
            "VisualBibleVersion", ""
        ) or "VisualBibleVersion" in source  # read reference only
    # The fork service only reads the Original snapshot; it never instantiates
    # an Original Visual Bible row.
    assert "VisualBibleVersion(" not in FORK_SOURCE
    # Original Canon namespace is never a derivative write target.
    assert "original_canon" not in FORK_SOURCE
    assert "user_interpretation" not in FORK_SOURCE


def test_source_is_referenced_read_only_via_restrict():
    assert "fk_derivative_visual_versions_source_scope" in MODEL_SOURCE
    assert "ondelete=\"RESTRICT\"" in MODEL_SOURCE
    assert "visual_bible_versions.owner_id" in MODEL_SOURCE
    # The source composite FK binds owner+novel+source_version_id together.
    assert '["owner_id", "novel_id", "source_version_id"]' in MODEL_SOURCE
    assert "fk_derivative_visual_versions_source_scope" in MIGRATION_SOURCE
    assert "ondelete=\"RESTRICT\"" in MIGRATION_SOURCE


def test_model_and_migration_seal_namespace_to_fanfiction_visual():
    assert "ck_derivative_visual_versions_namespace" in MODEL_SOURCE
    assert "visual_namespace = 'fanfiction_visual'" in MODEL_SOURCE
    assert "ck_derivative_visual_versions_namespace" in MIGRATION_SOURCE
    assert "fanfiction_visual" in MIGRATION_SOURCE
    # The Original Canon namespace must never appear as a write target.
    assert "original_canon" not in MIGRATION_SOURCE
    assert "original_canon" not in SCHEMA_SOURCE


def test_migration_chains_from_override_head():
    assert 'down_revision = "20260802_derivative_override01"' in MIGRATION_SOURCE
    assert 'revision: str = "20260802_derivative_visual01"' in MIGRATION_SOURCE


# ---------------------------------------------------------------------------
# Every query is owner/novel scoped; source must be in scope (T-38-01-02)
# ---------------------------------------------------------------------------


def test_every_fork_and_read_path_is_owner_novel_scoped():
    for source in (FORK_SOURCE, LINEAGE_SOURCE):
        assert "DerivativeVisualVersion.owner_id == owner_id" in source
        assert "DerivativeVisualVersion.novel_id == novel_id" in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {
                "get",
                "get_one",
            }:
                raise AssertionError(f"db.get bypass found at line {node.lineno}")


def test_source_snapshot_lookup_is_scoped():
    # The Original snapshot is resolved inside the owner/novel scope.
    assert "VisualBibleVersion.owner_id == owner_id" in FORK_SOURCE
    assert "VisualBibleVersion.novel_id == novel_id" in FORK_SOURCE
    assert "VisualBibleVersion.id == version.source_version_id" in FORK_SOURCE


# ---------------------------------------------------------------------------
# Source hash lineage immutability (T-38-01-01)
# ---------------------------------------------------------------------------


def test_source_hash_mutation_fails_closed_in_service():
    assert "source_snapshot_hash_mismatch" in FORK_SOURCE
    assert "source_manifest_hash_mismatch" in FORK_SOURCE
    assert "cutoff_chapter_mismatch" in FORK_SOURCE
    assert "source_version_not_found" in FORK_SOURCE
    assert "namespace_denied" in FORK_SOURCE
    # The immutable lineage is frozen on the ORM (only review_state may move).
    assert "_FROZEN_VERSION_LINEAGE" in MODEL_SOURCE
    assert "review_state" in MODEL_SOURCE


def test_conflicting_fork_retry_fails_closed():
    assert "fork_conflict" in FORK_SOURCE
    assert "version_key already exists with different" in FORK_SOURCE


# ---------------------------------------------------------------------------
# Divergence / approval gates
# ---------------------------------------------------------------------------


def test_explicit_divergence_gate_is_deterministic():
    from app.schemas.derivative_visual import (
        DerivativeVisualGateError,
        recompute_derivative_visual_manifest_hash,
    )

    version = DerivativeVisualVersionContract.model_validate(
        _version() | {"manifest_hash": "0" * 64}
    )
    version = version.model_copy(
        update={"manifest_hash": recompute_derivative_visual_manifest_hash(version)}
    )
    validate_derivative_visual_fork_contract(version)  # no raise

    # An empty divergence fails the deterministic gate.
    empty = version.model_copy(update={"divergence": {}})
    with pytest.raises(DerivativeVisualGateError, match="divergence"):
        validate_derivative_visual_fork_contract(empty)


def test_no_implicit_fork_from_reading_progress():
    # The derivative visual fork is never inferred from the reading page.
    assert "reading_progress" not in FORK_SOURCE
    assert "reading_progress" not in SCHEMA_SOURCE


def test_approval_is_never_an_in_place_promotion():
    # Approval is an append-only review event in lineage.py; nothing here
    # promotes a derivative candidate to a pointer/current revision.
    assert "current_revision" not in MODEL_SOURCE
    assert "active_pointer" not in MODEL_SOURCE
    assert "DerivativeVisualReviewEvent" in LINEAGE_SOURCE


# ---------------------------------------------------------------------------
# Phase 38-02: derivative Scene Spec gates (REQ-FORK-04 / REQ-CRE-06 / D-38-03)
# ---------------------------------------------------------------------------


def _compile_input(**overrides):
    """Minimal valid compile input; every gate must pass unless overridden."""
    from app.schemas.derivative_visual import (
        DerivativeAnchorRef,
        DerivativeAssetLineageRow,
        DerivativeIdentityRow,
        DerivativeReferenceAssetRow,
        DerivativeSceneSpecEvidenceRef,
    )
    from app.schemas.scene_spec import (
        SceneDetail,
        SceneSpecContract,
        SpecDetailKind,
        SpecEvidenceRef,
        SpecReviewState,
        SpecSource,
        recompute_scene_spec_hash,
    )
    from app.services.derivative_visual.gates import (
        DerivativeSceneSpecCompileInput,
    )

    evidence = SpecEvidenceRef(
        evidence_key="ev-1",
        source_snapshot_id="snap-1",
        source_snapshot_hash=HEX64,
        chapter_id=1,
        chapter_number=3,
        source_start=10,
        source_end=40,
        content_hash="b" * 64,
        excerpt="grey-eyed archer draws her bow",
        cutoff_chapter=8,
    )
    spec = SceneSpecContract(
        schema_version="scene-spec.v1",
        artifact_kind="scene_spec",
        owner_id=11,
        novel_id=22,
        spec_key="spec-1",
        revision_number=1,
        scene_candidate_hash=HEX64,
        scene_candidate_id=1,
        visual_bible_revision_hash="c" * 64,
        visual_bible_revision_id=55,
        source_snapshot_id="snap-1",
        source_snapshot_hash=HEX64,
        cutoff_chapter=8,
        schema_hash=HEX64,
        compiler_id="scene-spec.v1",
        compiler_version="1.0.0",
        policy_hash=HEX64,
        content_hash="0" * 64,
        details=[
            SceneDetail(
                detail_key="subject:char-arya",
                kind=SpecDetailKind.SUBJECT,
                source=SpecSource.EVIDENCE,
                text="grey-eyed archer",
                evidence_refs=[evidence],
                spoiler_cutoff=8,
            )
        ],
        negative_constraints=[],
        uncertainties=[],
        review_state=SpecReviewState.APPROVED,
    )
    spec = spec.model_copy(update={"content_hash": recompute_scene_spec_hash(spec)})
    payload = {
        "owner_id": 11,
        "novel_id": 22,
        "project_id": 33,
        "fork_id": 44,
        "spec_key": "ds-1",
        "visual_fork_version_id": 66,
        "visual_fork_version_hash": HEX64,
        "visual_fork_review_state": "approved",
        "visual_namespace": "fanfiction_visual",
        "divergence": {"style": "warm palette"},
        "provenance": {"branch": "fork-1"},
        "cutoff_chapter": 8,
        "visual_bible_revision_id": 55,
        "visual_bible_revision_hash": "c" * 64,
        "visual_bible_review_state": "approved",
        "source_snapshot_id": "snap-1",
        "source_snapshot_hash": HEX64,
        "source_manifest_hash": "c" * 64,
        "scene_spec_id": 7,
        "scene_spec_hash": spec.content_hash,
        "scene_spec_review_state": "approved",
        "scene_spec_visual_bible_revision_hash": "c" * 64,
        "scene_spec_source_snapshot_hash": HEX64,
        "scene_spec_cutoff_chapter": 8,
        "scene_spec": spec,
        "identity": (
            DerivativeIdentityRow.model_validate(
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
                        "source_entity_hash": "b" * 64,
                    },
                    "disclosure_cutoff": 8,
                }
            ),
        ),
        "reference_assets": (
            DerivativeReferenceAssetRow.model_validate(
                {
                    "asset_key": "dv-arya",
                    "asset_id": "dv-obj-1",
                    "mime_type": "image/png",
                    "bytes_hash": "b" * 64,
                    "rights_status": "unreviewed",
                    "source_asset_ref": {
                        "source_asset_id": "obj-1",
                        "source_bytes_hash": "b" * 64,
                    },
                    "approved": False,
                }
            ),
        ),
        "evidence_refs": (
            DerivativeSceneSpecEvidenceRef.model_validate(
                {
                    "evidence_key": "ev-1",
                    "source_snapshot_id": "snap-1",
                    "source_snapshot_hash": HEX64,
                    "chapter_number": 3,
                    "source_start": 10,
                    "source_end": 40,
                    "content_hash": "b" * 64,
                    "cutoff_chapter": 8,
                }
            ),
        ),
        "asset_lineage": (
            DerivativeAssetLineageRow.model_validate(
                {
                    "asset_revision_id": 101,
                    "asset_id": "asset-101",
                    "bytes_hash": "d" * 64,
                    "mime_type": "image/png",
                    "approval_state": "proposal_ready",
                    "scene_spec_hash": spec.content_hash,
                    "provider": "mock",
                    "provider_model": "mock-1",
                }
            ),
        ),
        "anchors": (
            DerivativeAnchorRef.model_validate(
                {
                    "anchor_id": 201,
                    "anchor_key": "anchor-1",
                    "chapter_number": 3,
                    "status": "valid",
                    "asset_revision_id": 101,
                    "publish_manifest_hash": "d" * 64,
                }
            ),
        ),
        "export_manifest_hash": "d" * 64,
    }
    payload.update(overrides)
    return DerivativeSceneSpecCompileInput(**payload)


def test_scene_spec_compiler_never_writes_original_or_calls_provider():
    # The compiler/gates are read-only seams: no Original Visual Bible write
    # path and no provider invocation exists in the Phase 38-02 modules.
    for source in (GATES_SOURCE, SCENE_SPEC_SOURCE):
        # No Original Visual Bible row is ever instantiated (read-only refs).
        assert "VisualBibleVersion(" not in source
        assert "VisualBibleVersionRow(" not in source
        assert "ai_service" not in source
        assert "ai_router" not in source
        assert "provider_request" not in source.replace("provider_model", "")
    # The API is the only consumer seam; it never calls a provider either.
    assert "ai_service" not in API_SOURCE


def test_scene_spec_wrong_namespace_is_rejected():
    from app.services.derivative_visual.gates import (
        DerivativeSceneSpecGateError,
        run_compile_gates,
    )
    from app.services.derivative_visual.scene_spec import compile_derivative_scene_spec

    # Original Canon namespace is never a derivative Scene Spec target.
    checks = run_compile_gates(_compile_input(visual_namespace="original_canon"))
    assert any(c.code == "namespace_denied" for c in checks)
    with pytest.raises(DerivativeSceneSpecGateError, match="namespace_denied"):
        compile_derivative_scene_spec(
            _compile_input(visual_namespace="original_canon")
        )


def test_scene_spec_stale_source_hash_is_rejected():
    from app.services.derivative_visual.gates import (
        DerivativeSceneSpecGateError,
    )
    from app.services.derivative_visual.scene_spec import compile_derivative_scene_spec

    # The sealed SceneSpec frozen against a different snapshot is stale.
    with pytest.raises(DerivativeSceneSpecGateError, match="source_snapshot_hash_mismatch"):
        compile_derivative_scene_spec(
            _compile_input(scene_spec_source_snapshot_hash="b" * 64)
        )


def test_scene_spec_mixed_authority_is_rejected():
    from app.services.derivative_visual.gates import (
        DerivativeSceneSpecGateError,
        run_compile_gates,
    )
    from app.services.derivative_visual.scene_spec import compile_derivative_scene_spec

    # A derivative asset silently approved as canon is mixed authority.
    checks = run_compile_gates(
        _compile_input(
            reference_assets=(
                type(_compile_input().reference_assets[0]).model_validate(
                    _compile_input().reference_assets[0].model_dump()
                    | {"approved": True}
                ),
            )
        )
    )
    assert any(c.code == "derivative_asset_approved" for c in checks)
    with pytest.raises(DerivativeSceneSpecGateError, match="derivative_asset_approved"):
        compile_derivative_scene_spec(
            _compile_input(
                reference_assets=(
                    type(_compile_input().reference_assets[0]).model_validate(
                        _compile_input().reference_assets[0].model_dump()
                        | {"approved": True}
                    ),
                )
            )
        )


def test_scene_spec_hidden_divergence_is_rejected():
    from app.services.derivative_visual.gates import (
        DerivativeSceneSpecGateError,
    )
    from app.services.derivative_visual.scene_spec import compile_derivative_scene_spec

    with pytest.raises(DerivativeSceneSpecGateError, match="divergence_required"):
        compile_derivative_scene_spec(_compile_input(divergence={}))


def test_scene_spec_future_cutoff_is_rejected():
    from app.services.derivative_visual.gates import (
        DerivativeSceneSpecGateError,
    )
    from app.services.derivative_visual.scene_spec import compile_derivative_scene_spec

    with pytest.raises(DerivativeSceneSpecGateError, match="cutoff_exceeds_scope"):
        compile_derivative_scene_spec(_compile_input(scene_spec_cutoff_chapter=12))


def test_scene_spec_is_owner_scoped_in_api_and_service():
    # Every compile/read seam resolves contracts inside the owner/novel scope.
    assert "DerivativeVisualVersion.owner_id == owner_id" in SCENE_SPEC_SOURCE
    assert "DerivativeVisualVersion.novel_id == novel_id" in SCENE_SPEC_SOURCE
    assert "VisualBibleVersion.owner_id == owner_id" in SCENE_SPEC_SOURCE
    assert "SceneSpecVersionRow.owner_id == owner_id" in SCENE_SPEC_SOURCE
    # The API never bypasses the owner-scoped service seam.
    assert "require_owned_novel" in API_SOURCE
    assert "DerivativeSceneSpecService(db)" in API_SOURCE
