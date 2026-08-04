"""Phase 38-02 derivative Scene Spec compiler and gate tests (REQ-FORK-04/REQ-CRE-06).

Covers D-38-01/D-38-02/D-38-03:
- the canonical derivative Scene Spec fixes identity/style/reference/divergence/
  cutoff/evidence/hash fields, is the only provider input and never carries an
  Original Visual Bible row or a file path;
- the same frozen snapshot always produces the same content hash and every
  reference is re-verifiable (no ORM object leakage);
- namespace / source-hash / cutoff / divergence / identity / mixed-authority /
  implicit-canon / upstream gates block the compile with stable reason codes
  before any provider call;
- negative constraints from the sealed SceneSpec and the branch fork are
  carried explicitly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.derivative_visual import (
    DERIVATIVE_SCENE_SPEC_SCHEMA_VERSION,
    DerivativeAnchorRef,
    DerivativeAssetLineageRow,
    DerivativeIdentityRow,
    DerivativeNegativeConstraint,
    DerivativeReferenceAssetRow,
    DerivativeSceneSpecContract,
    DerivativeSceneSpecEvidenceRef,
    recompute_derivative_scene_spec_hash,
)
from app.schemas.scene_spec import (
    ConstraintScope,
    NegativeConstraint,
    SceneDetail,
    SceneSpecContract,
    SceneUncertainty,
    SpecDetailKind,
    SpecEvidenceRef,
    SpecReviewState,
    SpecSource,
    UncertaintyReason,
    VisualBibleRef,
    recompute_scene_spec_hash,
)
from app.services.derivative_visual.gates import (
    DerivativeSceneSpecCompileInput,
    DerivativeSceneSpecGateError,
    assert_spec_gates_pass,
    run_compile_gates,
)
from app.services.derivative_visual.scene_spec import (
    compile_derivative_scene_spec,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64

SNAPSHOT_ID = "snap-1"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_scene_spec(
    *,
    owner_id: int = 11,
    novel_id: int = 22,
    spec_key: str = "spec-1",
    cutoff: int = 8,
    evidence_cutoff: int | None = None,
    with_uncertainty: bool = False,
    with_constraint: bool = True,
) -> SceneSpecContract:
    """A valid sealed SceneSpec contract (approved, replayable hash)."""
    ref = SpecEvidenceRef(
        evidence_key="ev-1",
        source_snapshot_id=SNAPSHOT_ID,
        source_snapshot_hash=HEX64,
        chapter_id=1,
        chapter_number=3,
        source_start=10,
        source_end=40,
        content_hash=HEX64_B,
        excerpt="grey-eyed archer draws her bow in the rain",
        cutoff_chapter=evidence_cutoff or cutoff,
    )
    details = [
        SceneDetail(
            detail_key="subject:char-arya",
            kind=SpecDetailKind.SUBJECT,
            source=SpecSource.EVIDENCE,
            text="grey-eyed archer draws her bow",
            evidence_refs=[ref],
            spoiler_cutoff=cutoff,
        )
    ]
    constraints = []
    if with_constraint:
        constraints = [
            NegativeConstraint(
                constraint_key="no-modern-clothes",
                scope=ConstraintScope.STYLE,
                source=SpecSource.VISUAL_BIBLE,
                text="no modern clothing",
                visual_bible_refs=[
                    VisualBibleRef(stable_id="style", revision_hash=HEX64_C)
                ],
                spoiler_cutoff=cutoff,
            )
        ]
    uncertainties = []
    if with_uncertainty:
        uncertainties = [
            SceneUncertainty(
                uncertainty_key="u-1",
                reason=UncertaintyReason.FUTURE_SPOILER,
                detail="a later reveal is withheld from this scene",
            )
        ]
    spec = SceneSpecContract(
        schema_version="scene-spec.v1",
        artifact_kind="scene_spec",
        owner_id=owner_id,
        novel_id=novel_id,
        spec_key=spec_key,
        revision_number=1,
        scene_candidate_hash=HEX64,
        scene_candidate_id=1,
        visual_bible_revision_hash=HEX64_C,
        visual_bible_revision_id=55,
        source_snapshot_id=SNAPSHOT_ID,
        source_snapshot_hash=HEX64,
        cutoff_chapter=cutoff,
        schema_hash=HEX64,
        compiler_id="scene-spec.v1",
        compiler_version="1.0.0",
        policy_hash=HEX64,
        config_hash=None,
        content_hash="0" * 64,
        details=details,
        negative_constraints=constraints,
        uncertainties=uncertainties,
        review_state=SpecReviewState.APPROVED,
    )
    return spec.model_copy(
        update={"content_hash": recompute_scene_spec_hash(spec)}
    )


def _identity(**overrides):
    payload = {
        "stable_id": "char-arya",
        "entity_key": "char-arya",
        "entity_type": "character",
        "description": "grey-eyed archer with a bow",
        "authority": "canon_fact",
        "divergence": {"palette": "soft greys"},
        "source_entity_ref": {
            "source_entity_id": 7,
            "source_entity_key": "char-arya",
            "source_entity_hash": HEX64_B,
        },
        "disclosure_cutoff": 8,
    }
    payload.update(overrides)
    return DerivativeIdentityRow.model_validate(payload)


def _asset(**overrides):
    payload = {
        "asset_key": "dv-arya-sketch",
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
    payload.update(overrides)
    return DerivativeReferenceAssetRow.model_validate(payload)


def _evidence(**overrides):
    payload = {
        "evidence_key": "ev-1",
        "source_snapshot_id": SNAPSHOT_ID,
        "source_snapshot_hash": HEX64,
        "chapter_number": 3,
        "source_start": 10,
        "source_end": 40,
        "content_hash": HEX64_B,
        "cutoff_chapter": 8,
    }
    payload.update(overrides)
    return DerivativeSceneSpecEvidenceRef.model_validate(payload)


def _asset_lineage(**overrides):
    payload = {
        "asset_revision_id": 101,
        "asset_id": "asset-101",
        "bytes_hash": HEX64_D,
        "mime_type": "image/png",
        "approval_state": "proposal_ready",
        "scene_spec_hash": "0" * 64,  # patched by the builder below
        "provider": "mock",
        "provider_model": "mock-1",
    }
    payload.update(overrides)
    return DerivativeAssetLineageRow.model_validate(payload)


def _anchor(**overrides):
    payload = {
        "anchor_id": 201,
        "anchor_key": "anchor-1",
        "chapter_number": 3,
        "status": "valid",
        "asset_revision_id": 101,
        "publish_manifest_hash": HEX64_D,
    }
    payload.update(overrides)
    return DerivativeAnchorRef.model_validate(payload)


def _input(**overrides):
    if "scene_spec" in overrides:
        spec = overrides["scene_spec"]
    else:
        spec = _make_scene_spec()
    spec_hash = spec.content_hash if spec is not None else ""
    overrides.setdefault("scene_spec", spec)
    overrides.setdefault("scene_spec_hash", spec_hash)
    overrides.setdefault("scene_spec_visual_bible_revision_hash", HEX64_C)
    overrides.setdefault("scene_spec_source_snapshot_hash", HEX64)
    overrides.setdefault("scene_spec_cutoff_chapter", 8)
    overrides.setdefault("identity", (_identity(),))
    overrides.setdefault("reference_assets", (_asset(),))
    overrides.setdefault(
        "derivative_negative_constraints",
        (
            DerivativeNegativeConstraint(
                constraint_key="branch-no-cold-palette",
                scope="style",
                source="derivative",
                text="keep the warm palette",
            ),
        ),
    )
    overrides.setdefault("evidence_refs", (_evidence(),))
    overrides.setdefault("uncertainties", ())
    if spec is not None:
        overrides.setdefault("asset_lineage", (_asset_lineage(scene_spec_hash=spec_hash),))
        overrides.setdefault("anchors", (_anchor(),))
    else:
        overrides.setdefault("asset_lineage", ())
        overrides.setdefault("anchors", ())
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
        "divergence": {"style": "warm palette", "note": "branch A"},
        "provenance": {"branch": "fork-1", "project": "proj-1"},
        "cutoff_chapter": 8,
        "style_profile": {"palette": "warm"},
        "visual_bible_revision_id": 55,
        "visual_bible_revision_hash": HEX64_C,
        "visual_bible_review_state": "approved",
        "source_snapshot_id": SNAPSHOT_ID,
        "source_snapshot_hash": HEX64,
        "source_manifest_hash": HEX64_C,
        "scene_spec_id": 7,
        "scene_spec_review_state": "approved",
        "export_manifest_hash": HEX64_D,
    }
    payload.update(overrides)
    return DerivativeSceneSpecCompileInput(**payload)


# ---------------------------------------------------------------------------
# Golden fixture: canonical fields + replayable hash
# ---------------------------------------------------------------------------


def test_valid_input_passes_all_eight_gates():
    checks = run_compile_gates(_input())
    assert [c.gate for c in checks] == [
        "upstream",
        "namespace",
        "source_hash",
        "cutoff",
        "divergence",
        "identity",
        "mixed_authority",
        "implicit_canon",
    ]
    assert all(c.ok for c in checks)
    assert_spec_gates_pass(checks)  # no raise


def test_compile_produces_frozen_canonical_spec():
    spec = compile_derivative_scene_spec(_input())
    assert spec.schema_version == DERIVATIVE_SCENE_SPEC_SCHEMA_VERSION
    assert spec.artifact_kind == "derivative_scene_spec"
    assert spec.visual_namespace == "fanfiction_visual"
    assert spec.review_state == "candidate"
    # identity/style/reference/divergence/cutoff/evidence/hash are fixed.
    assert len(spec.identity) == 1
    assert spec.identity[0].stable_id == "char-arya"
    assert spec.style_profile == {"palette": "warm"}
    assert spec.divergence["style"] == "warm palette"
    assert spec.cutoff_chapter == 8
    assert len(spec.evidence_refs) == 1
    assert spec.evidence_refs[0].evidence_key == "ev-1"
    assert len(spec.anchors) == 1
    assert spec.anchors[0].anchor_key == "anchor-1"
    assert spec.export_manifest_hash == HEX64_D
    assert spec.content_hash == recompute_derivative_scene_spec_hash(spec)
    # The frozen contract replays its own hash on re-validation.
    assert DerivativeSceneSpecContract.model_validate(spec.model_dump())


def test_same_snapshot_same_hash_divergence_sensitivity():
    a = compile_derivative_scene_spec(_input())
    b = compile_derivative_scene_spec(_input())
    assert a.content_hash == b.content_hash
    changed = compile_derivative_scene_spec(
        _input(divergence={"style": "cold palette"})
    )
    assert changed.content_hash != a.content_hash


def test_spec_is_provider_only_no_orm_leakage():
    spec = compile_derivative_scene_spec(_input())
    dumped = spec.model_dump(mode="json")
    # Every reference is an id/hash pin; no ORM row or file path can appear.
    import json

    text = json.dumps(dumped)
    for forbidden in (
        "visual_bible_versions",
        "storage_key",
        "cover_url",
        "VisualBibleVersion",
        "chapter.content",
    ):
        assert forbidden not in text
    assert spec.visual_fork_version_id == 66
    assert spec.scene_spec_id == 7
    assert spec.scene_spec_hash == _input().scene_spec.content_hash


def test_negative_constraints_are_carried_explicitly():
    spec = compile_derivative_scene_spec(_input())
    keys = [(c.constraint_key, c.source) for c in spec.negative_constraints]
    assert ("scene-spec:no-modern-clothes", "scene_spec") in keys
    assert ("branch-no-cold-palette", "derivative") in keys


# ---------------------------------------------------------------------------
# Contract-level rejection (extra="forbid", sealed namespace)
# ---------------------------------------------------------------------------


def test_contract_rejects_original_namespace_and_extra_fields():
    with pytest.raises(ValidationError):
        DerivativeSceneSpecContract.model_validate(
            _input_contract_payload() | {"visual_namespace": "original_canon"}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeSceneSpecContract.model_validate(
            _input_contract_payload() | {"original_canon": True}
        )


def _input_contract_payload():
    spec = compile_derivative_scene_spec(_input())
    return spec.model_dump()


# ---------------------------------------------------------------------------
# Gate negatives (blocked before any compile / provider call)
# ---------------------------------------------------------------------------


def test_wrong_namespace_is_blocked():
    with pytest.raises(DerivativeSceneSpecGateError, match="namespace_denied"):
        compile_derivative_scene_spec(
            _input(visual_namespace="original_canon")
        )


def test_stale_source_snapshot_hash_is_blocked():
    with pytest.raises(DerivativeSceneSpecGateError, match="source_snapshot_hash_mismatch"):
        compile_derivative_scene_spec(
            _input(scene_spec_source_snapshot_hash=HEX64_B)
        )


def test_source_manifest_hash_mismatch_is_blocked():
    with pytest.raises(DerivativeSceneSpecGateError, match="source_manifest_hash_mismatch"):
        compile_derivative_scene_spec(
            _input(source_manifest_hash=HEX64_B)
        )


def test_scene_spec_visual_bible_revision_mismatch_is_blocked():
    with pytest.raises(DerivativeSceneSpecGateError, match="scene_spec_visual_bible_revision_mismatch"):
        compile_derivative_scene_spec(
            _input(scene_spec_visual_bible_revision_hash=HEX64_B)
        )


def test_future_cutoff_is_blocked():
    with pytest.raises(DerivativeSceneSpecGateError, match="cutoff_exceeds_scope"):
        compile_derivative_scene_spec(
            _input(scene_spec_cutoff_chapter=12)
        )


def test_identity_disclosure_beyond_cutoff_is_blocked():
    with pytest.raises(DerivativeSceneSpecGateError, match="identity_disclosure_beyond_cutoff"):
        compile_derivative_scene_spec(
            _input(identity=(_identity(disclosure_cutoff=12),))
        )


def test_undeclared_style_divergence_is_blocked():
    with pytest.raises(DerivativeSceneSpecGateError, match="divergence_required"):
        compile_derivative_scene_spec(_input(divergence={}))


def test_identity_drift_is_blocked():
    with pytest.raises(DerivativeSceneSpecGateError, match="identity_source_ref_missing"):
        compile_derivative_scene_spec(
            _input(
                identity=(
                    _identity(
                        source_entity_ref={
                            "source_entity_id": 7,
                            "source_entity_key": "char-arya",
                        }
                    ),
                )
            )
        )
    with pytest.raises(DerivativeSceneSpecGateError, match="duplicate_stable_id"):
        compile_derivative_scene_spec(
            _input(
                identity=(
                    _identity(),
                    _identity(stable_id="char-arya", entity_key="char-arya-2"),
                )
            )
        )


def test_mixed_authority_is_blocked():
    # A derivative asset can never be silently approved (D-38-03).
    with pytest.raises(DerivativeSceneSpecGateError, match="derivative_asset_approved"):
        compile_derivative_scene_spec(
            _input(reference_assets=(_asset(approved=True),))
        )
    # A reused original path without its bytes hash is blocked.
    with pytest.raises(DerivativeSceneSpecGateError, match="asset_source_ref_missing"):
        compile_derivative_scene_spec(
            _input(
                reference_assets=(
                    _asset(
                        source_asset_ref={
                            "source_asset_id": "obj-1",
                        }
                    ),
                )
            )
        )


def test_asset_lineage_bound_to_sealed_spec_only():
    # A lineage row bound to a different scene spec hash is blocked.
    with pytest.raises(DerivativeSceneSpecGateError, match="asset_lineage_spec_mismatch"):
        compile_derivative_scene_spec(
            _input(asset_lineage=(_asset_lineage(scene_spec_hash=HEX64_B),))
        )


def test_implicit_canon_detail_is_blocked():
    # A sealed scene spec whose evidence lineage no longer replays fails the
    # revalidation gate — an unbacked detail can never become canon.
    spec = _make_scene_spec(evidence_cutoff=12)  # ref.cutoff != spec.cutoff
    with pytest.raises(DerivativeSceneSpecGateError, match="implicit_canon_detail"):
        compile_derivative_scene_spec(
            _input(scene_spec=spec, scene_spec_hash=spec.content_hash)
        )


def test_uncertainties_dropped_is_blocked():
    spec = _make_scene_spec(with_uncertainty=True)
    with pytest.raises(DerivativeSceneSpecGateError, match="uncertainties_dropped"):
        compile_derivative_scene_spec(
            _input(
                scene_spec=spec,
                scene_spec_hash=spec.content_hash,
                uncertainties=(),
            )
        )
    # Carrying the uncertainty compiles fine.
    from app.schemas.derivative_visual import DerivativeSceneSpecUncertainty

    compiled = compile_derivative_scene_spec(
        _input(
            scene_spec=spec,
            scene_spec_hash=spec.content_hash,
            uncertainties=(
                DerivativeSceneSpecUncertainty(
                    uncertainty_key="u-1",
                    reason="future_spoiler",
                    detail="a later reveal is withheld from this scene",
                ),
            ),
        )
    )
    assert len(compiled.uncertainties) == 1


def test_unapproved_upstream_is_blocked():
    with pytest.raises(DerivativeSceneSpecGateError, match="visual_fork_not_approved"):
        compile_derivative_scene_spec(
            _input(visual_fork_review_state="candidate")
        )
    with pytest.raises(DerivativeSceneSpecGateError, match="visual_bible_source_not_approved"):
        compile_derivative_scene_spec(
            _input(visual_bible_review_state="candidate")
        )
    with pytest.raises(DerivativeSceneSpecGateError, match="scene_spec_not_approved"):
        compile_derivative_scene_spec(
            _input(scene_spec_review_state="candidate")
        )
    with pytest.raises(DerivativeSceneSpecGateError, match="scene_spec_missing"):
        compile_derivative_scene_spec(_input(scene_spec=None))


def test_gate_failure_report_is_auditable():
    input_ = _input(visual_namespace="original_canon")
    checks = run_compile_gates(input_)
    failing = [c for c in checks if not c.ok]
    assert failing and failing[0].code == "namespace_denied"
    try:
        compile_derivative_scene_spec(input_)
    except DerivativeSceneSpecGateError as exc:
        assert exc.code == "namespace_denied"
        assert exc.gate == "namespace"
        assert any(c.code == "namespace_denied" for c in exc.checks)
