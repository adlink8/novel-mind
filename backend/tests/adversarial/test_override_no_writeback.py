"""Phase 37-04 adversarial no-writeback + contract freeze tests (red team).

D-37-03 / D-37-04 / REQ-CRE-06 / REQ-FORK-03: these red-team gates prove, with
deterministic pure functions and AST source checks (no PostgreSQL):

- the override/published modules never write Original Canon / User
  Interpretation / Narrative Memory and never grant a production promotion or
  a quality qualification (D-37-04: Phase 22 nightly qualification stays
  independent — generation success is not a promotion);
- the immutable ``PublishedDerivativeRevision`` DTO field set is frozen by a
  cross-plan contract test (a Phase 39 consumer cannot silently widen/rename
  the contract), the dataclass is immutable and the status is derivative-only;
- an override without reason/evidence/approval is rejected at the pure
  surface (missing-reason guard is hit before any DB access);
- the divergence approval is not conflated with a publication approval
  (``publish_derivative_revision`` / ``allow_divergence`` never appear);
- no third-party dependency is introduced (T-37-04-SC).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services.derivative_generation.overrides import (
    override_hash,
)
from app.services.derivative_generation.published_revision import (
    DERIVATIVE_REVISION_PUBLICATION_STATUS,
    PUBLISHED_DERIVATIVE_REVISION_FIELDS,
    PublishedDerivativeRevision,
    canonical_citation_hash,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

SERVICE_DIR = (
    Path(__file__).resolve().parents[2] / "app" / "services" / "derivative_generation"
)
MODELS_DIR = Path(__file__).resolve().parents[2] / "app" / "models"
OVERRIDES_SOURCE = (SERVICE_DIR / "overrides.py").read_text(encoding="utf-8")
PUBLISHED_SOURCE = (SERVICE_DIR / "published_revision.py").read_text(encoding="utf-8")
MODEL_SOURCE = (MODELS_DIR / "derivative_override.py").read_text(encoding="utf-8")

HEX64 = "a" * 64


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
    return modules


# ---------------------------------------------------------------------------
# Immutable PublishedDerivativeRevision DTO contract freeze (Phase 39 consumer)
# ---------------------------------------------------------------------------


def test_published_revision_dto_field_set_is_frozen():
    """Cross-plan contract: the DTO field set never silently changes."""
    actual = {
        field.name
        for field in __import__("dataclasses").fields(PublishedDerivativeRevision)
    }
    assert actual == set(PUBLISHED_DERIVATIVE_REVISION_FIELDS), (
        "PublishedDerivativeRevision DTO fields drifted from the frozen contract"
    )


def test_published_revision_dto_is_immutable():
    dto = PublishedDerivativeRevision(
        owner_id=1,
        project_id=2,
        fork_id=3,
        revision_id=4,
        version_id=5,
        status=DERIVATIVE_REVISION_PUBLICATION_STATUS,
        source_snapshot=HEX64,
        manifest_hash=HEX64,
        citation_hash=HEX64,
        asset_hashes=[],
        approval={"approval_state": "approved"},
        review={"gate_verdict": "needs_override"},
    )
    with pytest.raises(Exception):
        dto.status = "promoted"  # type: ignore[misc]
    assert dto.status == DERIVATIVE_REVISION_PUBLICATION_STATUS


def test_published_revision_status_is_derivative_only_never_promoted():
    assert DERIVATIVE_REVISION_PUBLICATION_STATUS == "derivative_revision"
    assert DERIVATIVE_REVISION_PUBLICATION_STATUS not in (
        "original",
        "promoted",
        "published",
    )


def test_citation_hash_is_deterministic_and_order_independent():
    a = canonical_citation_hash(["fork:1", "fork:2"])
    b = canonical_citation_hash(["fork:2", "fork:1"])
    assert a == b
    assert len(a) == 64
    assert all(c in "0123456789abcdef" for c in a)


def test_override_hash_is_deterministic_and_64_hex():
    h1 = override_hash(
        kind="character",
        reason="twist",
        affected_evidence=["fork:1"],
        package_hash=HEX64,
    )
    h2 = override_hash(
        kind="character",
        reason="twist",
        affected_evidence=["fork:1"],
        package_hash=HEX64,
    )
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)
    h3 = override_hash(
        kind="character",
        reason="twist",
        affected_evidence=["fork:1", "fork:2"],
        package_hash=HEX64,
    )
    assert h1 != h3


# ---------------------------------------------------------------------------
# D-37-03 fail-closed surface: missing reason/evidence/approval never passes
# ---------------------------------------------------------------------------


def test_override_service_rejects_missing_reason_before_db_access():
    """The missing-reason guard fires with no DB session (pure fail closed)."""
    import asyncio

    from app.services.derivative_generation.overrides import (
        OverrideError,
        create_override,
    )

    with pytest.raises(OverrideError) as exc_info:
        asyncio.run(
            create_override(
                db=None,  # type: ignore[arg-type] - must fail before touching it
                owner_id=1,
                novel_id=1,
                project_id=1,
                chapter_id=1,
                candidate_id=1,
                reason="   ",
                affected_evidence=["fork:1"],
                actor_id=1,
            )
        )
    assert exc_info.value.code == "missing_reason"


def test_missing_evidence_and_approval_codes_are_stable():
    from app.services.derivative_generation.overrides import (
        CODE_MISSING_APPROVAL,
        CODE_MISSING_EVIDENCE,
        CODE_MISSING_KIND,
        CODE_MISSING_REASON,
    )

    assert CODE_MISSING_REASON == "missing_reason"
    assert CODE_MISSING_EVIDENCE == "missing_evidence"
    assert CODE_MISSING_APPROVAL == "missing_approval"
    assert CODE_MISSING_KIND == "missing_kind"


# ---------------------------------------------------------------------------
# No-writeback: Original / Interpretation / NM / promotion never reachable
# ---------------------------------------------------------------------------


def test_override_service_never_writes_original_or_interpretation():
    """No Original Canon / User Interpretation write model is reachable."""
    for source in (OVERRIDES_SOURCE, PUBLISHED_SOURCE, MODEL_SOURCE):
        for forbidden in (
            "CanonSpaceArtifact(",
            "original_canon",
            "user_interpretation",
            "NarrativeUnit(",
            "NarrativeMemoryNode(",
            "ClueActivePointer(",
            "TimelineActivePointer(",
        ):
            assert forbidden not in source, f"{forbidden!r} must not appear in 37-04"


def test_override_approval_is_never_a_publication_or_qualification_promotion():
    """D-37-04/D-37-05: the divergence approval never promotes to Phase 39
    publication or Phase 22 quality qualification."""
    for source in (OVERRIDES_SOURCE, PUBLISHED_SOURCE):
        for forbidden in (
            "publish_derivative_revision",
            "allow_divergence",
            "NarrativePromotionJournal(",
            "QualityRun(",
            "ActiveBaseline(",
            "narrative_memory_qualification",
        ):
            assert forbidden not in source, f"{forbidden!r} must not appear in 37-04"


def test_published_module_is_db_free_and_provider_free():
    """published_revision.py imports no ORM session, gateway or network stack."""
    for forbidden in (
        "sqlalchemy",
        "AsyncSession",
        "litellm",
        "openai",
        "httpx",
        "asyncio",
    ):
        assert forbidden not in _imported_modules(PUBLISHED_SOURCE), (
            f"published_revision must not import {forbidden!r}"
        )
    assert "session.add" not in PUBLISHED_SOURCE
    assert "self._session" not in PUBLISHED_SOURCE


def test_override_model_freezes_the_divergence_surface():
    """The frozen field guard covers every divergence surface column."""
    assert "_FROZEN_OVERRIDE_FIELDS" in MODEL_SOURCE
    for field_name in (
        "kind",
        "reason",
        "affected_evidence",
        "canon_delta_hash",
        "evidence_snapshot",
        "candidate_id",
        "actor_id",
    ):
        assert f'"{field_name}"' in MODEL_SOURCE, (
            f"override model must freeze {field_name!r}"
        )
    # The review journal is explicitly allowed to change (approval action).
    assert "approval_state" in MODEL_SOURCE
    assert "approver_id" in MODEL_SOURCE


def test_no_third_party_dependency_introduced():
    """T-37-04-SC: 37-04 adds no package install (stdlib + app imports only)."""
    allowed = {
        "__future__",
        "dataclasses",
        "datetime",
        "hashlib",
        "json",
        "typing",
        "pydantic",
        "sqlalchemy",
    }
    for source in (OVERRIDES_SOURCE, PUBLISHED_SOURCE):
        for module in _imported_modules(source):
            assert module in allowed or module.startswith("app"), (
                f"unexpected third-party import {module!r} in 37-04 module"
            )
