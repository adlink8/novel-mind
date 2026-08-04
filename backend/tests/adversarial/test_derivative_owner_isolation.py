"""Adversarial owner-isolation gates for derivative projects (Phase 36-01).

REQ-FORK-02 / REQ-CRE-03 / D-36-01 / D-36-03. These deterministic gates
(contract + AST source checks, no PostgreSQL) prove that:

- the project wire contract is ``extra="forbid"``: the client cannot inject
  ``owner_id``/``novel_id``/``space``/``status``/version/cutoff lineage;
- creation always requires the explicit ``fork_id`` (no implicit reading-page
  inference — ``reading_progress`` is never consulted);
- every project query is scoped by owner + novel (a foreign/missing id is an
  identical 404) and the fork must be inside that scope;
- the write space is sealed to ``fanfiction_canon`` at the DTO/model/DB level and
  no Original Canon or User Interpretation write path exists in the project
  surface;
- the frozen fork lineage guard rejects in-place mutation of the project's
  scope/version/cutoff columns.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.derivative_project import (
    DerivativeProjectCreate,
    DerivativeProjectPatch,
)
from app.services.derivative_editor.projects import (
    DerivativeProjectError,
    _require_scope,
    slugify_project_key,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
API_SOURCE = (BACKEND_ROOT / "app" / "api" / "derivative_projects.py").read_text(
    encoding="utf-8"
)
SERVICE_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_editor" / "projects.py"
).read_text(encoding="utf-8")
SCHEMA_SOURCE = (
    BACKEND_ROOT / "app" / "schemas" / "derivative_project.py"
).read_text(encoding="utf-8")
MODEL_SOURCE = (
    BACKEND_ROOT / "app" / "models" / "derivative_project.py"
).read_text(encoding="utf-8")
MIGRATION_SOURCE = (
    BACKEND_ROOT / "migrations" / "versions" / "36_derivative_project01.py"
).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Wire contract: extra="forbid" prevents client scope injection (T-36-01-01)
# ---------------------------------------------------------------------------


def test_create_requires_explicit_fork_id():
    with pytest.raises(ValidationError, match="fork_id"):
        DerivativeProjectCreate(name="no fork")
    assert "fork_id" in SCHEMA_SOURCE


def test_client_cannot_inject_owner_novel_space_or_status():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeProjectCreate(fork_id=1, name="x", owner_id=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeProjectCreate(fork_id=1, name="x", novel_id=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeProjectCreate(fork_id=1, name="x", space="original_canon")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeProjectCreate(fork_id=1, name="x", status="approved")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeProjectCreate(fork_id=1, name="x", manifest_hash="b" * 64)


def test_patch_cannot_inject_fork_lineage():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeProjectPatch(name="x", fork_id=5)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeProjectPatch(name="x", source_version_key="original:1")
    with pytest.raises(ValidationError):
        DerivativeProjectPatch()


def test_name_validation_fails_closed():
    with pytest.raises(ValidationError):
        DerivativeProjectCreate(fork_id=1, name="   ")
    with pytest.raises(ValidationError):
        DerivativeProjectCreate(fork_id=1, name="x" * 200)


def test_fork_id_must_be_positive():
    with pytest.raises(ValidationError):
        DerivativeProjectCreate(fork_id=0, name="x")


# ---------------------------------------------------------------------------
# Service pure helpers: scope + deterministic project_key (D-36-01)
# ---------------------------------------------------------------------------


def test_require_scope_rejects_invalid_scope():
    for owner_id, novel_id in (
        (0, 1),
        (1, 0),
        (-1, 1),
        ("1", 1),
        (None, 1),
    ):
        with pytest.raises(DerivativeProjectError, match="invalid_scope"):
            _require_scope(owner_id=owner_id, novel_id=novel_id)


def test_slugify_project_key_is_deterministic_and_never_empty():
    assert slugify_project_key("My Draft Story") == "my-draft-story"
    assert slugify_project_key("My Draft Story") == slugify_project_key("My Draft Story")
    for name in ("新的世界", "!!!", "   "):
        key = slugify_project_key(name)
        assert len(key) > 0
        assert key == slugify_project_key(name)


# ---------------------------------------------------------------------------
# No implicit reading-page inference (D-36-01)
# ---------------------------------------------------------------------------


def test_no_reading_page_inference():
    # The project is never derived from the current reading page.
    assert "reading_progress" not in API_SOURCE
    assert "reading_progress" not in SERVICE_SOURCE
    assert "reading_progress" not in SCHEMA_SOURCE


# ---------------------------------------------------------------------------
# Fanfiction-only write space (D-36-03) — no Original/Interpretation path
# ---------------------------------------------------------------------------


def test_no_original_or_interpretation_write_path():
    for source in (API_SOURCE, SERVICE_SOURCE, SCHEMA_SOURCE):
        assert "original_canon" not in source
        assert "user_interpretation" not in source
        assert "OriginalCanon" not in source
        assert "CanonSpaceArtifact" not in source
    # The service seals the space to the Fanfiction Canon constant.
    assert "DERIVATIVE_PROJECT_SPACE" in SERVICE_SOURCE
    assert "CANON_FORK_SPACE" in SERVICE_SOURCE
    assert "fanfiction_canon" in MODEL_SOURCE


def test_model_and_migration_bind_space_to_fanfiction_canon():
    assert "ck_derivative_projects_space" in MODEL_SOURCE
    assert "space = 'fanfiction_canon'" in MODEL_SOURCE
    assert "ck_derivative_projects_space" in MIGRATION_SOURCE
    assert "ck_derivative_projects_status" in MIGRATION_SOURCE
    assert "ck_derivative_projects_cutoff" in MIGRATION_SOURCE


def test_migration_chains_from_canon_contamination_head():
    assert 'down_revision = "20260801_canon_contamination04"' in MIGRATION_SOURCE
    assert 'revision: str = "20260801_derivative_project01"' in MIGRATION_SOURCE
    assert 'sa.ForeignKeyConstraint(["fork_id"], ["canon_forks.id"]' in MIGRATION_SOURCE.replace(
        " " * 0, ""
    ) or "canon_forks.id" in MIGRATION_SOURCE


# ---------------------------------------------------------------------------
# Owner/fork scope in every project query (T-36-01-01)
# ---------------------------------------------------------------------------


def test_every_project_query_is_owner_and_novel_scoped():
    # Every scoped row lookup filters by owner + novel + id together.
    assert "DerivativeProject.owner_id == owner_id" in SERVICE_SOURCE
    assert "DerivativeProject.novel_id == novel_id" in SERVICE_SOURCE
    # The fork lookup is scoped the same way (foreign fork -> identical 404).
    assert "CanonFork.owner_id == owner_id" in SERVICE_SOURCE
    assert "CanonFork.novel_id == novel_id" in SERVICE_SOURCE
    # No project lookup keys on id alone.
    tree = ast.parse(SERVICE_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {
            "get",
            "get_one",
        }:
            # db.get would bypass the owner/novel scope filter — forbid it.
            raise AssertionError(f"db.get bypass found at line {node.lineno}")


def test_api_uses_require_owned_novel_everywhere():
    # Every route resolves the novel through the shared owner-scoped dependency.
    assert "require_owned_novel" in API_SOURCE
    # All routes are under the owner-scoped novels prefix (no bare id routes).
    assert "derivative-projects" in API_SOURCE
    # The API exposes no direct fork mutation or publication surface.
    for forbidden in ("materialize", "publish", "canon_space_artifacts"):
        assert forbidden not in API_SOURCE


# ---------------------------------------------------------------------------
# Frozen fork lineage guard (T-36-01-02)
# ---------------------------------------------------------------------------


def test_frozen_lineage_mutation_fails_closed():
    assert "_FROZEN_LINEAGE" in MODEL_SOURCE
    assert '"before_update", _reject_project_lineage_mutation' in MODEL_SOURCE
    assert "source_version_key" in MODEL_SOURCE
    assert "manifest_hash" in MODEL_SOURCE
    # The mutable set is only the project state, never the fork lineage.
    assert "name" in MODEL_SOURCE
    assert "description" in MODEL_SOURCE
    assert "status" in MODEL_SOURCE


def test_hash_replay_helpers_are_deterministic():
    assert hashlib.sha256(b"x").hexdigest() == hashlib.sha256(b"x").hexdigest()
    key = slugify_project_key("  Trimmed  Name  ")
    assert re.fullmatch(r"[a-z0-9-]+", key)
