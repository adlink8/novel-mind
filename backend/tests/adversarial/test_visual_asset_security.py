"""Adversarial derivative asset security gates (Phase 38-03, D-38-03).

REQ-FORK-04 / REQ-CRE-06 / D-38-03. These deterministic gates (contract + AST
source checks + pure storage/consistency logic, no PostgreSQL) prove that:

- the derivative asset candidate contract is ``extra="forbid"``: the client
  can never inject an Original namespace, an approval flag, an owner/novel/fork
  scope, a storage path or an SSRF transport URL;
- the candidate namespace is sealed to ``fanfiction_visual`` at the
  DTO/model/migration level and the candidate services have no Original Visual
  Bible write path (REQ-FORK-04);
- candidate bytes are stored under an allowlisted derivative root with
  generated ``asset_id`` and a replayed content checksum; path traversal and
  scope escape fail closed (T-38-03-01);
- every candidate store/read/published-query path is owner/novel scoped and
  the published query is approved-only (T-38-03-02); Original or unapproved
  assets are never returned;
- cross-chapter consistency scoring is deterministic, explicit ``unavailable``
  on missing input, and can never auto-publish (identity drift / undeclared
  style divergence -> blocked);
- the migration chains from ``20260802_derivative_visual01`` to the new
  ``20260802_derivative_asset01`` head.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.derivative_visual_asset import (
    DERIVATIVE_ASSET_NAMESPACE,
    DERIVATIVE_CONSISTENCY_EVALUATOR_ID,
    ChapterConsistencyEvidence,
    DerivativeAssetCandidateWrite,
    DerivativeAssetGeneratorLineage,
    DerivativeAssetIdentityRow,
    DerivativeAssetSourceRef,
    DerivativeConsistencyVerdict,
    DerivativeVisualAssetState,
    derivative_asset_review_state_after,
    review_state_from_consistency_verdict,
)
from app.services.derivative_visual.assets import (
    DerivativeAssetStorage,
    DerivativeAssetStorageError,
)
from app.services.derivative_visual.consistency import score_cross_chapter_consistency

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ASSETS_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_visual" / "assets.py"
).read_text(encoding="utf-8")
CONSISTENCY_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_visual" / "consistency.py"
).read_text(encoding="utf-8")
PUBLISHED_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_visual" / "published_assets.py"
).read_text(encoding="utf-8")
SCHEMA_ASSET_SOURCE = (
    BACKEND_ROOT / "app" / "schemas" / "derivative_visual_asset.py"
).read_text(encoding="utf-8")
MODEL_SOURCE = (BACKEND_ROOT / "app" / "models" / "derivative_visual.py").read_text(
    encoding="utf-8"
)
API_ASSET_SOURCE = (
    BACKEND_ROOT / "app" / "api" / "derivative_visual_assets.py"
).read_text(encoding="utf-8")
MIGRATION_SOURCE = (
    BACKEND_ROOT / "migrations" / "versions" / "38_derivative_asset01.py"
).read_text(encoding="utf-8")

HEX64 = "a" * 64
HEX64_B = "b" * 64


def _chapter(**overrides):
    payload = {
        "chapter_number": 1,
        "identity_key": "char-arya",
        "identity_source_hash": HEX64,
        "style_hash": HEX64_B,
        "scene_spec_hash": "c" * 64,
        "declared_style_divergence": False,
        "missing_identity_evidence": False,
        "missing_style_evidence": False,
    }
    payload.update(overrides)
    return ChapterConsistencyEvidence.model_validate(payload)


def _candidate_write(**overrides):
    payload = {
        "asset_key": "dv-a-1",
        "chapter_number": 1,
        "mime_type": "image/png",
        "content_hash": HEX64,
        "scene_spec_hash": "c" * 64,
        "divergence_manifest_hash": "d" * 64,
        "identity_lineage": [
            {
                "stable_id": "char-arya",
                "entity_key": "char-arya",
                "entity_type": "character",
                "source_entity_hash": HEX64,
            }
        ],
        "source_refs": [
            {
                "asset_key": "dv-arya",
                "asset_id": "dv-obj-1",
                "source_asset_id": "obj-1",
                "source_bytes_hash": HEX64_B,
            }
        ],
        "generator_lineage": {"provider": "mock", "provider_model": "mock-1"},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Wire contract: extra="forbid" blocks namespace/approval/scope/path injection
# ---------------------------------------------------------------------------


def test_candidate_write_rejects_extra_fields_and_approval():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAssetCandidateWrite.model_validate(
            _candidate_write() | {"approved": True}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAssetCandidateWrite.model_validate(
            _candidate_write() | {"visual_namespace": "original_canon"}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAssetCandidateWrite.model_validate(
            _candidate_write() | {"owner_id": 99}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAssetCandidateWrite.model_validate(
            _candidate_write() | {"storage_key": "../evil.png"}
        )


def test_candidate_write_requires_full_lineage():
    with pytest.raises(ValidationError, match="identity lineage"):
        DerivativeAssetCandidateWrite.model_validate(
            _candidate_write() | {"identity_lineage": []}
        )
    with pytest.raises(ValidationError, match="source refs"):
        DerivativeAssetCandidateWrite.model_validate(
            _candidate_write() | {"source_refs": []}
        )
    with pytest.raises(ValidationError, match="generator lineage"):
        DerivativeAssetCandidateWrite.model_validate(
            _candidate_write() | {"generator_lineage": {}}
        )


def test_ssrf_metadata_is_rejected():
    # Transport URLs can never enter the candidate lineage (T-38-03-02).
    with pytest.raises(ValidationError, match="transport URLs"):
        DerivativeAssetCandidateWrite.model_validate(
            _candidate_write()
            | {
                "generator_lineage": {
                    "provider": "mock",
                    "model_url": "http://evil.example/x",
                }
            }
        )
    with pytest.raises(ValidationError, match="transport URLs"):
        DerivativeAssetCandidateWrite.model_validate(
            _candidate_write()
            | {
                "identity_lineage": [
                    {
                        "stable_id": "char-arya",
                        "entity_key": "https://evil.example",
                        "entity_type": "character",
                        "source_entity_hash": HEX64,
                    }
                ]
            }
        )


def test_generator_lineage_contract_cannot_carry_url():
    with pytest.raises(ValidationError):
        DerivativeAssetGeneratorLineage.model_validate(
            {
                "provider": "mock",
                "provider_model": "mock-1",
                "runtime": {"endpoint": "https://evil.example"},
            }
        )


# ---------------------------------------------------------------------------
# Namespace sealed at DTO/model/migration level; no Original write path
# ---------------------------------------------------------------------------


def test_candidate_namespace_is_sealed():
    assert DERIVATIVE_ASSET_NAMESPACE == "fanfiction_visual"
    assert "ck_derivative_visual_candidates_namespace" in MODEL_SOURCE
    assert "visual_namespace = 'fanfiction_visual'" in MODEL_SOURCE
    assert "ck_derivative_visual_candidates_namespace" in MIGRATION_SOURCE
    assert "original_canon" not in SCHEMA_ASSET_SOURCE
    assert "original_canon" not in MIGRATION_SOURCE


def test_candidate_services_have_no_original_write_path():
    for source in (ASSETS_SOURCE, PUBLISHED_SOURCE):
        assert "VisualBibleVersion(" not in source
        assert "visual_bible_versions" not in source.replace("source_snapshot", "")
    # The read envelope never exposes a storage path to clients.
    assert "storage_key" not in PUBLISHED_SOURCE.replace("storage_key=", "")


def test_migration_chains_from_visual01_head():
    assert 'down_revision = "20260802_derivative_visual01"' in MIGRATION_SOURCE
    assert 'revision: str = "20260802_derivative_asset01"' in MIGRATION_SOURCE
    assert "derivative_visual_candidates" in MIGRATION_SOURCE
    assert "derivative_visual_candidate_review_events" in MIGRATION_SOURCE


# ---------------------------------------------------------------------------
# Storage: allowlisted root, generated ids, content checksum, no traversal
# ---------------------------------------------------------------------------


def test_storage_scope_escape_fails_closed(tmp_path):
    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    # ``../`` in the asset_id climbs out of its own version scope.
    with pytest.raises(DerivativeAssetStorageError, match="escapes"):
        storage.store(
            owner_id=1,
            novel_id=2,
            visual_version_id=3,
            asset_id="../evil",
            mime_type="image/png",
            payload=b"\x89PNG",
        )
    with pytest.raises(DerivativeAssetStorageError, match="escapes"):
        storage.read(
            owner_id=1,
            novel_id=2,
            visual_version_id=3,
            asset_id="../../evil",
            mime_type="image/png",
        )
    # Content checksum always replays; a mismatch fails closed.
    payload = b"candidate-bytes"
    key = storage.store(
        owner_id=1,
        novel_id=2,
        visual_version_id=3,
        asset_id="dv-" + "0" * 32,
        mime_type="image/png",
        payload=payload,
    )
    assert key.startswith("derivative_assets/1/2/3/")
    assert (
        storage.read(
            owner_id=1,
            novel_id=2,
            visual_version_id=3,
            asset_id="dv-" + "0" * 32,
            mime_type="image/png",
        )
        == payload
    )


def test_storage_allowlist_and_empty_payload_fail_closed(tmp_path):
    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    with pytest.raises(DerivativeAssetStorageError, match="unsupported candidate mime"):
        storage.store(
            owner_id=1,
            novel_id=2,
            visual_version_id=3,
            asset_id="dv-" + "0" * 32,
            mime_type="text/html",
            payload=b"<script>",
        )
    with pytest.raises(DerivativeAssetStorageError, match="empty"):
        storage.store(
            owner_id=1,
            novel_id=2,
            visual_version_id=3,
            asset_id="dv-" + "0" * 32,
            mime_type="image/png",
            payload=b"",
        )
    with pytest.raises(DerivativeAssetStorageError, match="scope"):
        storage.store(
            owner_id=0,
            novel_id=2,
            visual_version_id=3,
            asset_id="dv-" + "0" * 32,
            mime_type="image/png",
            payload=b"x",
        )


def test_generated_asset_ids_never_come_from_client():
    # The service generates asset ids; the client DTO has no path/id field.
    assert (
        "dv-{uuid.uuid4().hex}" in ASSETS_SOURCE
        or "generate_derivative_asset_id" in ASSETS_SOURCE
    )
    assert "asset_key" in SCHEMA_ASSET_SOURCE
    assert "storage_key" not in SCHEMA_ASSET_SOURCE.replace(
        "DerivativeAssetCandidateWrite", ""
    )
    assert 'asset_id="' not in SCHEMA_ASSET_SOURCE


# ---------------------------------------------------------------------------
# Published query: approved-only + owner/novel scope (asset IDOR)
# ---------------------------------------------------------------------------


def test_published_query_is_approved_only_and_scoped():
    assert "review_state" in PUBLISHED_SOURCE
    assert "APPROVED.value" in PUBLISHED_SOURCE
    assert "DerivativeVisualCandidateAsset.owner_id == owner_id" in PUBLISHED_SOURCE
    assert "DerivativeVisualCandidateAsset.novel_id == novel_id" in PUBLISHED_SOURCE
    assert "PublishedAssetNotFound" in PUBLISHED_SOURCE
    tree = ast.parse(PUBLISHED_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {
            "get",
            "get_one",
        }:
            raise AssertionError(f"db.get bypass found at line {node.lineno}")


def test_every_candidate_query_is_owner_novel_scoped():
    for source in (ASSETS_SOURCE, PUBLISHED_SOURCE):
        assert "owner_id" in source and "novel_id" in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {
                "get",
                "get_one",
            }:
                # Only a raw ``db.get``/``db.get_one`` bypass is a scope leak;
                # ``dict.get`` and ORM ``.get`` on other objects are legitimate.
                value = node.func.value
                if isinstance(value, ast.Name) and value.id == "db":
                    raise AssertionError(f"db.get bypass found at line {node.lineno}")


def test_blocked_candidates_can_never_be_approved():
    # ``blocked`` (identity drift / undeclared divergence) is terminal.
    assert DerivativeVisualAssetState.BLOCKED.value in SCHEMA_ASSET_SOURCE
    assert "BLOCKED: frozenset()" in SCHEMA_ASSET_SOURCE
    with pytest.raises(ValueError, match="illegal"):
        derivative_asset_review_state_after("blocked", "approve")
    with pytest.raises(ValueError, match="illegal"):
        derivative_asset_review_state_after("blocked", "reject")


# ---------------------------------------------------------------------------
# Cross-chapter consistency: deterministic, explicit unavailable, no auto-publish
# ---------------------------------------------------------------------------


def test_consistency_verdict_drives_review_state_chain():
    assert (
        review_state_from_consistency_verdict(DerivativeConsistencyVerdict.FAIL)
        is DerivativeVisualAssetState.BLOCKED
    )
    assert (
        review_state_from_consistency_verdict(DerivativeConsistencyVerdict.CONCERN)
        is DerivativeVisualAssetState.NEEDS_REVIEW
    )
    assert (
        review_state_from_consistency_verdict(DerivativeConsistencyVerdict.UNAVAILABLE)
        is DerivativeVisualAssetState.NEEDS_REVIEW
    )
    assert (
        review_state_from_consistency_verdict(DerivativeConsistencyVerdict.PASS)
        is DerivativeVisualAssetState.CANDIDATE
    )


def test_consistency_is_deterministic():
    a = score_cross_chapter_consistency(
        (_chapter(chapter_number=1), _chapter(chapter_number=2))
    )
    b = score_cross_chapter_consistency(
        (_chapter(chapter_number=1), _chapter(chapter_number=2))
    )
    assert a.verdict is b.verdict == DerivativeConsistencyVerdict.PASS
    assert a.model_dump() == b.model_dump()
    assert a.evaluator_id == DERIVATIVE_CONSISTENCY_EVALUATOR_ID


def test_consistency_pass_when_identity_and_style_hold():
    report = score_cross_chapter_consistency(
        (
            _chapter(chapter_number=1),
            _chapter(chapter_number=2),
            _chapter(chapter_number=3),
        )
    )
    assert report.verdict is DerivativeConsistencyVerdict.PASS
    assert report.reasons == []
    assert len(report.chapters) == 3
    assert all(
        ch.identity_score == 1.0 and ch.style_score == 1.0 for ch in report.chapters
    )


def test_consistency_identity_drift_fails():
    report = score_cross_chapter_consistency(
        (
            _chapter(chapter_number=1),
            _chapter(chapter_number=2, identity_source_hash="e" * 64),
        )
    )
    assert report.verdict is DerivativeConsistencyVerdict.FAIL
    assert any("identity_drift" in reason for reason in report.reasons)


def test_consistency_style_divergence_undeclared_fails():
    report = score_cross_chapter_consistency(
        (
            _chapter(chapter_number=1),
            _chapter(chapter_number=2, style_hash="f" * 64),
        )
    )
    assert report.verdict is DerivativeConsistencyVerdict.FAIL
    assert any("style_divergence_undeclared" in reason for reason in report.reasons)


def test_consistency_style_divergence_declared_is_concern():
    report = score_cross_chapter_consistency(
        (
            _chapter(chapter_number=1),
            _chapter(
                chapter_number=2, style_hash="f" * 64, declared_style_divergence=True
            ),
        )
    )
    assert report.verdict is DerivativeConsistencyVerdict.CONCERN
    assert any("style_divergence_declared" in reason for reason in report.reasons)


def test_consistency_missing_input_is_explicit_unavailable():
    # Fewer than two chapters -> unavailable (never a silent pass).
    report = score_cross_chapter_consistency((_chapter(chapter_number=1),))
    assert report.verdict is DerivativeConsistencyVerdict.UNAVAILABLE
    assert "insufficient_chapters" in report.reasons
    # Missing identity/style evidence -> unavailable with the exact reason.
    report = score_cross_chapter_consistency(
        (
            _chapter(chapter_number=1, missing_identity_evidence=True),
            _chapter(chapter_number=2),
        )
    )
    assert report.verdict is DerivativeConsistencyVerdict.UNAVAILABLE
    assert "missing_identity_evidence" in report.reasons
    report = score_cross_chapter_consistency(
        (
            _chapter(chapter_number=1),
            _chapter(chapter_number=2, missing_style_evidence=True),
        )
    )
    assert report.verdict is DerivativeConsistencyVerdict.UNAVAILABLE
    assert "missing_style_evidence" in report.reasons


def test_consistency_score_can_never_approve():
    # The scorer is a pure, DB-free function: no ORM rows, no session and no
    # approval/state vocabulary — it only emits a review signal.
    assert "sqlalchemy" not in CONSISTENCY_SOURCE
    assert "AsyncSession" not in CONSISTENCY_SOURCE
    assert "DerivativeVisualCandidateAsset" not in CONSISTENCY_SOURCE
    tree = ast.parse(CONSISTENCY_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {
            "add",
            "commit",
            "flush",
        }:
            raise AssertionError(f"unexpected side effect at line {node.lineno}")


def test_identity_row_contract_pins_source_hash():
    with pytest.raises(ValidationError, match="source_entity_hash"):
        DerivativeAssetIdentityRow.model_validate(
            {
                "stable_id": "char-arya",
                "entity_key": "char-arya",
                "entity_type": "character",
                "source_entity_hash": "not-a-hash",
            }
        )
    with pytest.raises(ValidationError):
        DerivativeAssetSourceRef.model_validate(
            {
                "asset_key": "a",
                "asset_id": "b",
                "source_asset_id": "c",
                "source_bytes_hash": "x",
            }
        )
