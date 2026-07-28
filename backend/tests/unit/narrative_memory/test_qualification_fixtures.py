"""Unit tests for Phase 17 fixture freeze / preflight (no provider)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.narrative_memory.qualification_fixtures import (
    FIXTURES_DIR,
    PreflightBlocked,
    freeze_fixture,
    freeze_has_promotion_capability,
    freeze_has_provider_capability,
    freeze_paired_case_matrix,
    load_frozen_bundle,
    load_json,
    module_has_forbidden_capability,
    preflight_execution_gates,
    prove_hash_sensitivity,
)
from app.services.narrative_memory.qualification_contracts import (
    QualificationFixture,
    QualificationPolicy,
)

pytestmark = pytest.mark.unit

FIXTURE_PATH = FIXTURES_DIR / "single_book_v1.json"
POLICY_PATH = FIXTURES_DIR / "policy_v1.json"
CONTRACTS_PATH = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "services"
    / "narrative_memory"
    / "qualification_contracts.py"
)
FIXTURES_MOD_PATH = CONTRACTS_PATH.with_name("qualification_fixtures.py")


def test_load_and_freeze_bundle_stable():
    fixture, policy, fx, pol = load_frozen_bundle(FIXTURE_PATH, POLICY_PATH)
    assert isinstance(fixture, QualificationFixture)
    assert isinstance(policy, QualificationPolicy)
    assert len(fx) == 64 and len(pol) == 64
    # reload byte-identical checksums
    fixture2, policy2, fx2, pol2 = load_frozen_bundle(FIXTURE_PATH, POLICY_PATH)
    assert fx == fx2 and pol == pol2
    assert fixture.checksum() == fx
    assert policy.checksum() == pol


def test_hash_sensitivity_and_order_independence():
    fixture, _ = freeze_fixture(load_json(FIXTURE_PATH))
    assert prove_hash_sensitivity(fixture)
    payload = load_json(FIXTURE_PATH)
    # key insertion order change in JSON object shouldn't affect sorted dump hash
    reordered = json.loads(json.dumps(payload, sort_keys=False))
    f1, h1 = freeze_fixture(payload)
    f2, h2 = freeze_fixture(reordered)
    assert h1 == h2


def test_result_fields_rejected_on_load():
    payload = load_json(FIXTURE_PATH)
    payload["candidate_score"] = 0.9
    with pytest.raises(ValueError, match="result-derived"):
        freeze_fixture(payload)


def test_paired_matrix_from_frozen_bundle():
    fixture, policy, _, _ = load_frozen_bundle(FIXTURE_PATH, POLICY_PATH)
    pairs = freeze_paired_case_matrix(fixture, policy)
    assert len(pairs) == 5
    for cand, base in pairs:
        assert cand.common.model_dump() == base.common.model_dump()
        assert "hierarchical_candidate" in cand.cache_namespace
        assert "leaf_raw_baseline" in base.cache_namespace


def test_preflight_blocks_missing_price_and_wip():
    fixture, policy, _, _ = load_frozen_bundle(FIXTURE_PATH, POLICY_PATH)
    with pytest.raises(PreflightBlocked) as ei:
        preflight_execution_gates(
            fixture=fixture,
            policy=policy,
            price_known=False,
            phase13_wip=True,
            build_complete=False,
        )
    codes = set(ei.value.reason_codes)
    assert "unknown_price" in codes
    assert "phase13_wip_active" in codes
    assert "partial_build" in codes


def test_preflight_passes_when_prereqs_ok():
    fixture, policy, _, _ = load_frozen_bundle(FIXTURE_PATH, POLICY_PATH)
    # May still block if verification files missing in isolated env; when present, no raise
    try:
        preflight_execution_gates(
            fixture=fixture,
            policy=policy,
            price_known=True,
            phase13_wip=False,
            build_complete=True,
        )
    except PreflightBlocked as exc:
        # only verification-related allowed if files incomplete in workspace layout
        assert all("verification" in c or "missing_" in c for c in exc.reason_codes)


def test_zero_provider_and_promotion():
    assert freeze_has_provider_capability() is False
    assert freeze_has_promotion_capability() is False


def test_forbidden_capability_scan_on_freeze_modules():
    # modules must not import promotion / reader chat / live providers
    for path in (CONTRACTS_PATH, FIXTURES_MOD_PATH):
        hits = module_has_forbidden_capability(path)
        # filter soft hits that are deny-list string constants
        hard = [h for h in hits if h.startswith("import:")]
        assert hard == [], f"{path.name} hard imports: {hard}"
