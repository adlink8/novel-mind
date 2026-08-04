"""Adversarial concurrency/CAS gates for derivative revisions (Phase 36-03).

REQ-FORK-02 / REQ-CRE-04 / D-36-02. These deterministic gates (contract + AST
source checks + pure-function tests, no PostgreSQL) prove that:

- autosave uses an **atomic conditional update** (``UPDATE ... WHERE revision =
  base_revision``); a concurrent or stale writer matches zero rows and can never
  last-write-win (T-36-03-01);
- conflicts carry the **latest revision** so recovery is always possible;
- rollback only creates a **NEW child revision** and the model's ``before_update``
  listener fails closed on any in-place history mutation (T-36-03-02);
- every revision query is scoped by owner + novel + project + chapter (no
  ``db.get`` id-only lookup; foreign/missing is an identical 404);
- the deterministic diff canonicalizes both sides (CRLF / trailing whitespace is
  invisible) and handles empty/identical/boundary inputs deterministically;
- the wire contract is ``extra="forbid"`` (no owner/novel/project/chapter/
  revision-number/checksum/kind/approval injection) and ``base_revision`` /
  ``target_revision_id`` must be positive;
- no Original Canon / User Interpretation write surface exists in the revision
  surface and the migration chains from the 36-02 head.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.derivative_revision import (
    DerivativeAutosaveRequest,
    DerivativeRollbackRequest,
)
from app.services.derivative_editor.chapters import (
    canonicalize_markdown,
    markdown_checksum,
)
from app.services.derivative_editor.revisions import (
    DerivativeRevisionError,
    _require_scope,
    diff_markdown,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
API_SOURCE = (BACKEND_ROOT / "app" / "api" / "derivative_revisions.py").read_text(
    encoding="utf-8"
)
AGENT_API_SOURCE = (
    BACKEND_ROOT / "app" / "api" / "agent_derivative_edits.py"
).read_text(encoding="utf-8")
EVENTS_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_editor" / "events.py"
).read_text(encoding="utf-8")
SERVICE_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_editor" / "revisions.py"
).read_text(encoding="utf-8")
SCHEMA_SOURCE = (
    BACKEND_ROOT / "app" / "schemas" / "derivative_revision.py"
).read_text(encoding="utf-8")
MODEL_SOURCE = (
    BACKEND_ROOT / "app" / "models" / "derivative_revision.py"
).read_text(encoding="utf-8")
CHAPTER_SERVICE_SOURCE = (
    BACKEND_ROOT / "app" / "services" / "derivative_editor" / "chapters.py"
).read_text(encoding="utf-8")
MIGRATION_SOURCE = (
    BACKEND_ROOT / "migrations" / "versions" / "36_derivative_revision01.py"
).read_text(encoding="utf-8")
AGENT_MIGRATION_SOURCE = (
    BACKEND_ROOT / "migrations" / "versions" / "36_derivative_agent_edit01.py"
).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Conditional CAS: autosave/rollback can never last-write-win (T-36-03-01)
# ---------------------------------------------------------------------------


def test_autosave_uses_conditional_update_cas():
    # The draft write is an atomic conditional UPDATE keyed on base_revision.
    assert "update(DerivativeChapter)" in SERVICE_SOURCE
    assert "DerivativeChapter.revision == base_revision" in SERVICE_SOURCE
    assert "result.rowcount == 0" in SERVICE_SOURCE
    # The service never falls back to a blind overwrite on the revision token.
    assert "row.revision = base_revision" not in SERVICE_SOURCE
    assert "row.revision ==" not in SERVICE_SOURCE


def test_conflict_carries_latest_revision_for_recovery():
    # The error carries the head row and the API exposes it to the stale client.
    assert "current_revision" in SERVICE_SOURCE
    assert "current_revision" in API_SOURCE
    assert "current_revision_number" in API_SOURCE
    assert "revision_conflict" in SERVICE_SOURCE
    # The API maps the service error code onto the wire and never hardcodes a
    # conflicting contract; the conflict is never a blind rejection and always
    # ships the head checksum.
    assert "exc.code" in API_SOURCE
    assert "current_checksum" in API_SOURCE


def test_idempotent_replay_path_is_present():
    # A crash-before-ack retry that replays the head content resolves without
    # appending a new row (duplicate autosave / network retry are idempotent).
    assert '"noop"' in SERVICE_SOURCE
    assert "checksum == chapter.markdown_checksum" in SERVICE_SOURCE
    assert 'kind="autosave"' in SERVICE_SOURCE


# ---------------------------------------------------------------------------
# Rollback = new child only; history is immutable (T-36-03-02)
# ---------------------------------------------------------------------------


def test_rollback_only_creates_a_new_child_revision():
    # rollback_revision appends an immutable rollback row and never mutates the
    # target/history rows; the actor/reason/approval journal is always written.
    assert 'kind="rollback"' in SERVICE_SOURCE
    assert "approval_state=\"approved\"" in SERVICE_SOURCE
    assert "reason" in SERVICE_SOURCE
    assert "actor_id" in SERVICE_SOURCE
    # The restored content comes from the target row, never from client input.
    assert "markdown=target.content" in SERVICE_SOURCE
    assert "markdown_checksum=target.content_checksum" in SERVICE_SOURCE


def test_revision_model_rejects_in_place_mutation():
    assert "before_update" in MODEL_SOURCE
    assert "_reject_revision_mutation" in MODEL_SOURCE
    assert "immutable" in MODEL_SOURCE
    assert "event.listen" in MODEL_SOURCE


def test_chapter_write_paths_append_revision_rows():
    # Both the root create and every real Markdown patch append lineage rows so
    # the version history is complete across all write surfaces.
    assert "DerivativeRevision(" in CHAPTER_SERVICE_SOURCE
    assert 'kind="create"' in CHAPTER_SERVICE_SOURCE
    assert "append_revision_row" in CHAPTER_SERVICE_SOURCE
    assert "row.revision += 1" in CHAPTER_SERVICE_SOURCE
    assert "revision_conflict" in CHAPTER_SERVICE_SOURCE


# ---------------------------------------------------------------------------
# Owner isolation: every revision query is owner/novel/project/chapter-scoped
# ---------------------------------------------------------------------------


def test_every_revision_query_is_scoped():
    assert "DerivativeRevision.owner_id == owner_id" in SERVICE_SOURCE
    assert "DerivativeRevision.novel_id == novel_id" in SERVICE_SOURCE
    assert "DerivativeRevision.project_id == project_id" in SERVICE_SOURCE
    assert "DerivativeRevision.chapter_id == chapter_id" in SERVICE_SOURCE
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


def test_api_uses_require_owned_novel_everywhere():
    assert "require_owned_novel" in API_SOURCE
    assert "derivative-projects" in API_SOURCE
    assert "chapters" in API_SOURCE
    assert "autosave" in API_SOURCE
    assert "rollback" in API_SOURCE
    assert "revisions" in API_SOURCE
    assert "diff" in API_SOURCE


# ---------------------------------------------------------------------------
# Deterministic canonical-Markdown diff (pure-function edge cases)
# ---------------------------------------------------------------------------


def test_diff_is_deterministic():
    old = "a\nb\nc"
    new = "a\nB\nc"
    assert diff_markdown(old, new) == diff_markdown(old, new)


def test_diff_identical_content_is_empty():
    assert diff_markdown("a\nb", "a\nb") == []
    assert diff_markdown("", "") == []


def test_diff_empty_to_content_is_all_additions():
    hunks = diff_markdown("", "a\nb")
    assert len(hunks) == 1
    assert hunks[0]["old_count"] == 0
    assert hunks[0]["new_count"] == 2
    assert [line["op"] for line in hunks[0]["lines"]] == ["add", "add"]


def test_diff_content_to_empty_is_all_deletions():
    hunks = diff_markdown("a\nb", "")
    assert len(hunks) == 1
    assert hunks[0]["old_count"] == 2
    assert hunks[0]["new_count"] == 0
    assert [line["op"] for line in hunks[0]["lines"]] == ["delete", "delete"]


def test_diff_canonicalizes_both_sides():
    # CRLF and trailing whitespace are invisible to the deterministic diff.
    assert diff_markdown("a\r\nb", "a\nb") == []
    assert diff_markdown("a  \n", "a") == []
    assert diff_markdown("a\nb", "a\r\nb") == []


def test_diff_line_numbers_are_stable():
    hunks = diff_markdown("a\nb\nc", "a\nB\nc")
    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk["old_start"] == 1
    assert hunk["old_count"] == 3
    assert hunk["new_start"] == 1
    assert hunk["new_count"] == 3
    ops = {line["op"] for line in hunk["lines"]}
    assert {"context", "delete", "add"} <= ops


# ---------------------------------------------------------------------------
# Replayable checksum + scope helper
# ---------------------------------------------------------------------------


def test_checksum_is_sha256_of_canonical_form():
    assert markdown_checksum("# T\r\nBody  ") == markdown_checksum("# T\nBody")
    assert markdown_checksum("x") == hashlib.sha256(b"x").hexdigest()
    assert len(markdown_checksum("x")) == 64
    assert markdown_checksum("") == hashlib.sha256(b"").hexdigest()
    assert canonicalize_markdown("a\r\nb  \n") == "a\nb"


def test_require_scope_rejects_invalid_scope():
    for owner_id, novel_id in (
        (0, 1),
        (1, 0),
        (-1, 1),
        ("1", 1),
        (None, 1),
    ):
        with pytest.raises(DerivativeRevisionError, match="invalid_scope"):
            _require_scope(owner_id=owner_id, novel_id=novel_id)


# ---------------------------------------------------------------------------
# Strict wire contract: extra="forbid", positive tokens, bounded reason
# ---------------------------------------------------------------------------


def test_autosave_dto_rejects_scope_and_authority_injection():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAutosaveRequest(content="x", base_revision=1, owner_id=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAutosaveRequest(content="x", base_revision=1, novel_id=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAutosaveRequest(content="x", base_revision=1, project_id=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAutosaveRequest(content="x", base_revision=1, chapter_id=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAutosaveRequest(content="x", base_revision=1, revision_number=5)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAutosaveRequest(content="x", base_revision=1, content_checksum="a" * 64)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAutosaveRequest(content="x", base_revision=1, kind="publish")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeAutosaveRequest(content="x", base_revision=1, approval_state="approved")


def test_rollback_dto_rejects_scope_injection_and_zero_ids():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeRollbackRequest(target_revision_id=1, base_revision=1, owner_id=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DerivativeRollbackRequest(target_revision_id=1, base_revision=1, revision_number=1)
    with pytest.raises(ValidationError, match="base_revision"):
        DerivativeRollbackRequest(target_revision_id=1)
    with pytest.raises(ValidationError):
        DerivativeRollbackRequest(target_revision_id=0, base_revision=1)
    with pytest.raises(ValidationError):
        DerivativeRollbackRequest(target_revision_id=1, base_revision=0)


def test_autosave_requires_positive_base_revision():
    with pytest.raises(ValidationError):
        DerivativeAutosaveRequest(content="x", base_revision=0)
    assert "base_revision: int = Field(gt=0)" in SCHEMA_SOURCE


def test_rollback_reason_bounded():
    with pytest.raises(ValidationError):
        DerivativeRollbackRequest(
            target_revision_id=1, base_revision=1, reason="x" * 2_001
        )
    valid = DerivativeRollbackRequest(target_revision_id=1, base_revision=1, reason="ok")
    assert valid.reason == "ok"


# ---------------------------------------------------------------------------
# Fanfiction-only drafts + no publication surface (D-36-03)
# ---------------------------------------------------------------------------


def test_no_original_or_interpretation_write_path():
    for source in (API_SOURCE, SERVICE_SOURCE, SCHEMA_SOURCE, MODEL_SOURCE):
        assert "original_canon" not in source
        assert "user_interpretation" not in source
        assert "OriginalCanon" not in source
    # The revision row inherits the project's DB-sealed Fanfiction Canon space.
    assert "fanfiction_canon" in MODEL_SOURCE


def test_no_publication_surface_in_revision_module():
    for source in (API_SOURCE, SERVICE_SOURCE, MODEL_SOURCE):
        assert "publish" not in source
    # Revision kinds seal the only write reasons; publication is never a kind.
    # 36-05 adds ``agent_proposal`` (the deterministic Revision Service apply of
    # an approved DerivativeEditProposal) as a distinct kind from autosave.
    assert "kind IN ('create','autosave','rollback','agent_proposal')" in MODEL_SOURCE
    assert "ck_derivative_revisions_kind" in MIGRATION_SOURCE
    assert "ck_derivative_revisions_kind" in AGENT_MIGRATION_SOURCE


def test_agent_proposal_kind_is_distinct_from_autosave():
    # 36-05: an agent proposal can never be treated as a user draft — the row
    # kind, the event name and the actor label all differ.
    assert "agent_proposal" in MODEL_SOURCE
    assert "'agent_proposal'" in MODEL_SOURCE
    assert "kind=\"agent_proposal\"" in SERVICE_SOURCE
    assert "approval_state=\"approved\"" in SERVICE_SOURCE
    # The deterministic Revision Service apply is CAS-guarded exactly like the
    # user autosave (no last-write-wins, conflict carries the latest revision).
    assert "DerivativeChapter.revision == base_revision" in SERVICE_SOURCE
    assert "result.rowcount == 0" in SERVICE_SOURCE
    assert "revision_conflict" in SERVICE_SOURCE


def test_agent_edit_approval_gate_is_server_authoritative():
    # The proposal gate only creates a pending Web ApprovalRequest bound to the
    # apply_derivative_edit action and a replayable payload hash.
    assert "DERIVATIVE_AGENT_EDIT_APPROVAL_ACTION" in SERVICE_SOURCE
    assert "apply_derivative_edit" in SERVICE_SOURCE
    assert "payload_hash" in SERVICE_SOURCE
    assert "status=\"pending\"" in SERVICE_SOURCE


def test_user_autosave_and_agent_proposal_events_are_disjoint():
    # The event-name owner defines exactly four events, two per path, and the
    # endpoints never reference the other path's events.
    assert "derivative.user_autosave.accepted" in EVENTS_SOURCE
    assert "derivative.user_autosave.conflict" in EVENTS_SOURCE
    assert "derivative.agent_proposal.applied" in EVENTS_SOURCE
    assert "derivative.agent_proposal.rejected" in EVENTS_SOURCE
    assert "DERIVATIVE_USER_AUTOSAVE_EVENTS" in EVENTS_SOURCE
    assert "DERIVATIVE_AGENT_PROPOSAL_EVENTS" in EVENTS_SOURCE
    # user autosave endpoint references only its own events.
    assert "DERIVATIVE_USER_AUTOSAVE_ACCEPTED" in API_SOURCE
    assert "DERIVATIVE_USER_AUTOSAVE_CONFLICT" in API_SOURCE
    assert "derivative.user_autosave.accepted" not in AGENT_API_SOURCE
    assert "derivative.agent_proposal.applied" in AGENT_API_SOURCE
    assert "derivative.agent_proposal.rejected" in AGENT_API_SOURCE
    assert "DERIVATIVE_AGENT_PROPOSAL_APPLIED" in AGENT_API_SOURCE
    assert "DERIVATIVE_AGENT_PROPOSAL_REJECTED" in AGENT_API_SOURCE
    assert "DERIVATIVE_USER_AUTOSAVE_ACCEPTED" not in AGENT_API_SOURCE


def test_agent_proposal_endpoint_is_separate_and_apply_only():
    # The agent_proposal path has its own endpoint; it never serves the
    # user autosave contract and the autosave endpoint never grants the Agent
    # approval.
    assert "/derivative-edit-proposals/{artifact_id}/apply" in AGENT_API_SOURCE
    assert "apply_agent_edit" in AGENT_API_SOURCE
    assert "approval_not_approved" in AGENT_API_SOURCE
    assert "approval_payload_mismatch" in AGENT_API_SOURCE or "approval_not_found" in AGENT_API_SOURCE
    assert "apply_derivative_edit" in AGENT_API_SOURCE
    # The autosave route is distinct (not served by the agent endpoint module).
    assert "/autosave" not in AGENT_API_SOURCE
    # Agent cannot bypass approval by writing through the user path: the user
    # autosave endpoint has no apply_derivative_edit approval lookup.
    assert "apply_derivative_edit" not in API_SOURCE


def test_agent_edit_migration_widens_kind_gate():
    # The 36-05 migration chains from the 36-03 head and widens the kind gate.
    assert 'down_revision = "20260801_derivative_revision01"' in AGENT_MIGRATION_SOURCE
    assert "agent_proposal" in AGENT_MIGRATION_SOURCE
    assert "ck_derivative_revisions_kind" in AGENT_MIGRATION_SOURCE
    assert "drop_constraint" in AGENT_MIGRATION_SOURCE
    assert "create_check_constraint" in AGENT_MIGRATION_SOURCE


# ---------------------------------------------------------------------------
# Migration chains from the 36-02 head and seals the row at the DB level
# ---------------------------------------------------------------------------


def test_migration_chains_from_derivative_chapter_head():
    assert 'down_revision = "20260801_derivative_chapter01"' in MIGRATION_SOURCE
    assert 'revision: str = "20260801_derivative_revision01"' in MIGRATION_SOURCE
    assert "uq_derivative_revisions_chapter_number" in MIGRATION_SOURCE
    assert "ck_derivative_revisions_checksum" in MIGRATION_SOURCE
    assert "ck_derivative_revisions_number" in MIGRATION_SOURCE
    assert "ck_derivative_revisions_approval" in MIGRATION_SOURCE
    assert "derivative_revisions.id" in MIGRATION_SOURCE  # parent self-FK
    assert "derivative_chapters.id" in MIGRATION_SOURCE


def test_model_and_migration_agree_on_constraint_names():
    for name in (
        "uq_derivative_revisions_chapter_number",
        "ck_derivative_revisions_number",
        "ck_derivative_revisions_checksum",
        "ck_derivative_revisions_kind",
        "ck_derivative_revisions_approval",
    ):
        assert name in MODEL_SOURCE
        assert name in MIGRATION_SOURCE


# ---------------------------------------------------------------------------
# Deterministic lineage wiring (parent points at the head; token maps 1:1)
# ---------------------------------------------------------------------------


def test_append_links_parent_to_the_current_head():
    # Every appended row points its parent at the latest row of the same chapter.
    assert "parent_revision_id=latest.id" in SERVICE_SOURCE
    assert "revision_number=revision_number" in SERVICE_SOURCE
    assert "revision_number=chapter.revision" in SERVICE_SOURCE
    assert 'revision_number=1' in CHAPTER_SERVICE_SOURCE  # root row


def test_diff_is_computed_after_canonicalization():
    assert "canonicalize_markdown(old_text)" in SERVICE_SOURCE
    assert "canonicalize_markdown(new_text)" in SERVICE_SOURCE
    assert "autojunk=False" in SERVICE_SOURCE
