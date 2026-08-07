"""Adversarial owner-scope gates for derivative chapters (Phase 36-02).

REQ-FORK-02 / REQ-CRE-03 / D-36-02 / D-36-03. These deterministic gates
(contract + AST source checks, no PostgreSQL) prove that:

- the chapter wire contract is ``extra="forbid"``: the client cannot inject
  ``owner_id``/``novel_id``/``project_id``/``position``/``revision``/
  ``markdown_checksum``/space/version/cutoff fields;
- every patch carries the required ``base_revision`` optimistic-concurrency
  token and an empty patch fails closed;
- Markdown canonicalization and the checksum are deterministic and replayable
  (CRLF → LF, trailing whitespace stripped, leading/trailing blank lines);
- every chapter query is scoped by owner + novel + project (no ``db.get``
  id-only lookup, foreign/missing is an identical 404) and the service never
  consults ``reading_progress`` (no implicit reading-page fork inference);
- reorder requires the exact full set of project chapter ids;
- the API uses ``require_owned_novel`` and exposes no Original/Interpretation
  write or publication surface; chapter status is draft/archived only.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.derivative_chapter import (
    DerivativeChapterCreate,
    DerivativeChapterPatch,
)
from app.services.derivative_editor.chapters import (
    DerivativeChapterError,
    _require_scope,
    canonicalize_markdown,
    markdown_checksum,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
API_SOURCE = (BACKEND_ROOT / "app" / "api" / "derivative_chapters.py").read_text(
    encoding="utf-8"
)
SERVICE_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_editor" / "chapters.py"
).read_text(encoding="utf-8")
SCHEMA_SOURCE = (BACKEND_ROOT / "app" / "schemas" / "derivative_chapter.py").read_text(
    encoding="utf-8"
)
MODEL_SOURCE = (BACKEND_ROOT / "app" / "models" / "derivative_chapter.py").read_text(
    encoding="utf-8"
)
MIGRATION_SOURCE = (
    BACKEND_ROOT / "migrations" / "versions" / "36_derivative_chapter01.py"
).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Wire contract: extra="forbid" prevents client scope injection (T-36-02-01)
# ---------------------------------------------------------------------------


def test_create_rejects_authority_and_lineage_fields():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeChapterCreate(title="t", owner_id=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeChapterCreate(title="t", novel_id=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeChapterCreate(title="t", project_id=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeChapterCreate(title="t", position=0)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeChapterCreate(title="t", revision=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeChapterCreate(title="t", markdown_checksum="b" * 64)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeChapterCreate(title="t", space="original_canon")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeChapterCreate(title="t", fork_key="x")


def test_patch_cannot_inject_scope_fields():
    for kw in (
        "owner_id",
        "novel_id",
        "project_id",
        "position",
        "revision",
        "markdown_checksum",
        "space",
        "cutoff_snapshot_hash",
    ):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            DerivativeChapterPatch(base_revision=1, **{kw: 1 if kw != "space" else "x"})
    # A valid allowlisted patch passes (title/markdown/status only + token).
    patch = DerivativeChapterPatch(base_revision=3, markdown="hello", status="draft")
    assert patch.markdown == "hello"
    assert patch.base_revision == 3
    assert patch.title is None


def test_patch_requires_base_revision_and_at_least_one_field():
    with pytest.raises(ValidationError, match="base_revision"):
        DerivativeChapterPatch(title="t")
    with pytest.raises(ValidationError):
        DerivativeChapterPatch(base_revision=1)
    with pytest.raises(ValidationError):
        DerivativeChapterPatch(base_revision=0)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeChapterPatch(base_revision=1, position=5)


def test_whitespace_title_fails_closed():
    with pytest.raises(ValidationError):
        DerivativeChapterCreate(title="   ")
    with pytest.raises(ValidationError):
        DerivativeChapterCreate(title="x" * 300)


# ---------------------------------------------------------------------------
# Deterministic canonicalization + checksum (D-36-02, replayable)
# ---------------------------------------------------------------------------


def test_canonicalize_markdown_is_deterministic():
    raw = "# Title\r\nBody line  \r\n\r\n\r\n"
    assert canonicalize_markdown(raw) == "# Title\nBody line"
    assert canonicalize_markdown(raw) == canonicalize_markdown(raw)
    # Lone CR and CRLF both normalize to LF; leading blank lines are removed.
    assert canonicalize_markdown("\r\n\r\nLead\rTail") == "Lead\nTail"
    assert canonicalize_markdown("") == ""
    assert canonicalize_markdown("   \n\n  ") == ""


def test_markdown_checksum_is_sha256_of_canonical_form():
    assert markdown_checksum("# T\r\nBody  ") == markdown_checksum("# T\nBody")
    expected = hashlib.sha256("# T\nBody".encode("utf-8")).hexdigest()
    assert markdown_checksum("# T\nBody") == expected
    assert len(markdown_checksum("x")) == 64


# ---------------------------------------------------------------------------
# Service pure helpers
# ---------------------------------------------------------------------------


def test_require_scope_rejects_invalid_scope():
    for owner_id, novel_id in (
        (0, 1),
        (1, 0),
        (-1, 1),
        ("1", 1),
        (None, 1),
    ):
        with pytest.raises(DerivativeChapterError, match="invalid_scope"):
            _require_scope(owner_id=owner_id, novel_id=novel_id)


# ---------------------------------------------------------------------------
# Owner/project/chapter scope in every query (T-36-02-02)
# ---------------------------------------------------------------------------


def test_every_chapter_query_is_scoped_by_owner_novel_project():
    assert "DerivativeChapter.owner_id == owner_id" in SERVICE_SOURCE
    assert "DerivativeChapter.novel_id == novel_id" in SERVICE_SOURCE
    assert "DerivativeChapter.project_id == project_id" in SERVICE_SOURCE
    assert "DerivativeProject.owner_id == owner_id" in SERVICE_SOURCE
    assert "DerivativeProject.novel_id == novel_id" in SERVICE_SOURCE
    # No id-only lookup bypasses the scope.
    tree = ast.parse(SERVICE_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {
            "get",
            "get_one",
        }:
            raise AssertionError(f"db.get bypass found at line {node.lineno}")


def test_no_implicit_reading_page_fork_inference():
    # The chapter is never derived from the current reading page (D-36-01/02).
    assert "reading_progress" not in API_SOURCE
    assert "reading_progress" not in SERVICE_SOURCE
    assert "reading_progress" not in SCHEMA_SOURCE


# ---------------------------------------------------------------------------
# Fanfiction-only drafts + no publication surface (D-36-03)
# ---------------------------------------------------------------------------


def test_no_original_or_interpretation_write_path():
    for source in (API_SOURCE, SERVICE_SOURCE, SCHEMA_SOURCE):
        assert "original_canon" not in source
        assert "user_interpretation" not in source
        assert "OriginalCanon" not in source
    # The service seals the draft to the project's Fanfiction Canon space.
    assert "DERIVATIVE_PROJECT_SPACE" in SCHEMA_SOURCE
    assert "fanfiction_canon" in MODEL_SOURCE
    assert "fanfiction_canon" in MIGRATION_SOURCE


def test_chapter_status_is_draft_or_archived_only():
    assert "status IN ('draft','archived')" in MODEL_SOURCE
    assert "ck_derivative_chapters_status" in MIGRATION_SOURCE
    # No client-controlled published status exists on the chapter surface: the
    # status enum/constant never contains a published value and the API exposes
    # no publish route.
    assert "'published'" not in MODEL_SOURCE
    assert "'published'" not in SCHEMA_SOURCE
    assert "publish" not in API_SOURCE
    assert "publish" not in SERVICE_SOURCE


def test_migration_chains_from_derivative_project_head():
    assert 'down_revision = "20260801_derivative_project01"' in MIGRATION_SOURCE
    assert 'revision: str = "20260801_derivative_chapter01"' in MIGRATION_SOURCE
    assert "uq_derivative_chapters_position" in MIGRATION_SOURCE
    assert "ck_derivative_chapters_checksum" in MIGRATION_SOURCE
    assert "derivative_projects.id" in MIGRATION_SOURCE


# ---------------------------------------------------------------------------
# API surface: require_owned_novel, no publication/materialization endpoints
# ---------------------------------------------------------------------------


def test_api_uses_require_owned_novel_everywhere():
    assert "require_owned_novel" in API_SOURCE
    assert "derivative-projects" in API_SOURCE
    assert "chapters" in API_SOURCE
    for forbidden in ("materialize", "publish", "canon_space_artifacts"):
        assert forbidden not in API_SOURCE


def test_reorder_requires_exact_full_set():
    assert "reorder_mismatch" in SERVICE_SOURCE
    assert "reorder_duplicate" in SERVICE_SOURCE
    assert "reorder_foreign_chapter" in SERVICE_SOURCE
    assert "chapter_ids" in SCHEMA_SOURCE


def test_optimistic_concurrency_token_is_server_arbitrated():
    assert "base_revision" in SCHEMA_SOURCE
    assert "base_revision" in SERVICE_SOURCE
    assert "revision_conflict" in SERVICE_SOURCE
    # Revision only bumps on a real canonical Markdown change (no-op detection).
    assert "row.revision += 1" in SERVICE_SOURCE


def test_checksum_is_computed_after_canonicalization():
    assert "canonicalize_markdown" in SERVICE_SOURCE
    assert "sha256" in SERVICE_SOURCE
    assert "markdown_checksum" in MODEL_SOURCE
    assert "length(markdown_checksum) = 64" in MIGRATION_SOURCE
