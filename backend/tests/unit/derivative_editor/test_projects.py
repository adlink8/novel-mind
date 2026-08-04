"""Unit tests for the derivative project contract layer (Phase 36-01, D-36-01/03).

Pure-Python gates (no PostgreSQL): strict wire DTO validation, the explicit
fork-selection contract, deterministic project_key derivation, scope validation
and the frozen fork-lineage guard.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models.derivative_project import (
    DERIVATIVE_PROJECT_SPACE,
    DERIVATIVE_PROJECT_STATUSES,
    DERIVATIVE_PROJECT_USABLE_FORK_STATUSES,
    DerivativeProject,
)
from app.schemas.derivative_project import (
    DerivativeProjectCreate,
    DerivativeProjectPatch,
    DerivativeProjectStatus,
    DerivativeProjectView,
)
from app.services.derivative_editor.projects import (
    DerivativeProjectError,
    _require_scope,
    slugify_project_key,
)

pytestmark = pytest.mark.unit


class TestWireContract:
    def test_create_requires_fork_id(self):
        with pytest.raises(ValidationError, match="fork_id"):
            DerivativeProjectCreate(name="x")

    def test_create_forbids_scope_injection(self):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            DerivativeProjectCreate(fork_id=1, name="x", owner_id=9)
        with pytest.raises(ValidationError, match="extra_forbidden"):
            DerivativeProjectCreate(fork_id=1, name="x", novel_id=9)
        with pytest.raises(ValidationError, match="extra_forbidden"):
            DerivativeProjectCreate(fork_id=1, name="x", space="original_canon")

    def test_create_name_and_key_bounds(self):
        with pytest.raises(ValidationError):
            DerivativeProjectCreate(fork_id=1, name="   ")
        with pytest.raises(ValidationError):
            DerivativeProjectCreate(fork_id=1, name="x" * 121)
        with pytest.raises(ValidationError):
            DerivativeProjectCreate(fork_id=1, name="x", project_key="x" * 129)

    def test_patch_requires_at_least_one_field(self):
        with pytest.raises(ValidationError):
            DerivativeProjectPatch()
        patch = DerivativeProjectPatch(name="renamed")
        assert patch.name == "renamed"

    def test_patch_forbids_fork_lineage(self):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            DerivativeProjectPatch(name="x", fork_id=2)

    def test_view_is_fanfiction_scoped(self):
        view = DerivativeProjectView(
            id=1,
            owner_id=1,
            novel_id=2,
            fork_id=3,
            project_key="pk",
            name="P",
            status=DerivativeProjectStatus.ACTIVE,
            space=DERIVATIVE_PROJECT_SPACE,
            fork_key="ff",
            source_version_key="original:1",
            source_snapshot_hash="a" * 64,
            through_chapter=3,
            full_book_authorized=False,
            cutoff_snapshot_hash="b" * 64,
            scope_hash="c" * 64,
            manifest_hash="d" * 64,
            created_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 1),
        )
        assert view.space == "fanfiction_canon"


class TestServicePureGates:
    def test_scope_validation_fails_closed(self):
        for bad in ((0, 1), (1, 0), (-1, 1), ("1", 1), (None, 1), (1.0, 1)):
            with pytest.raises(DerivativeProjectError, match="invalid_scope"):
                _require_scope(owner_id=bad[0], novel_id=bad[1])
        _require_scope(owner_id=1, novel_id=2)

    def test_project_key_derivation_is_deterministic(self):
        assert slugify_project_key("My Draft") == "my-draft"
        assert slugify_project_key("My Draft") == slugify_project_key("My Draft")
        for name in ("新的世界", "!!!", ""):
            key = slugify_project_key(name)
            assert key and key == slugify_project_key(name)

    def test_status_and_fork_usability_vocabulary(self):
        assert DERIVATIVE_PROJECT_STATUSES == ("active", "archived")
        assert DERIVATIVE_PROJECT_USABLE_FORK_STATUSES == ("candidate", "approved")


class TestModelContract:
    def test_space_and_status_constraints_exist(self):
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[3] / "app" / "models" / "derivative_project.py"
        ).read_text(encoding="utf-8")
        assert "space = 'fanfiction_canon'" in source
        assert "status IN ('active','archived')" in source
        assert "ck_derivative_projects_space" in source
        assert "before_update" in source
        # The project row itself stays a normal CRUD row (no delete guard).
        assert "before_delete" not in source

    def test_model_exports_project_type(self):
        assert DerivativeProject.__tablename__ == "derivative_projects"
        cols = {c.name for c in DerivativeProject.__table__.columns}
        assert {"fork_id", "project_key", "source_version_key", "manifest_hash"} <= cols
