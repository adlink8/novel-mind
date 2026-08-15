"""Owner-scoped derivative chapter revision service (Phase 36-03, D-36-02).

The service owns the append-only revision lineage of a chapter:

- **Autosave with conditional CAS (no last-write-wins, T-36-03-01):** the draft
  write is an atomic ``UPDATE ... WHERE revision = base_revision``. A stale or
  concurrent writer matches zero rows and is rejected with the current revision
  row (409) instead of overwriting newer content. A crash-before-ack retry that
  replays the exact head content resolves **idempotently** (``noop``, no new
  row) so retries never duplicate history or lose a draft.
- **Immutable history:** every real Markdown change appends a sealed
  ``DerivativeRevision`` row (``parent_revision_id``/``revision_number``/
  ``content_checksum``) inside the same transaction; existing rows are never
  mutated or deleted (the model's ``before_update`` listener fails closed).
- **Deterministic diff:** ``diff_markdown`` canonicalizes both sides first and
  emits a stable unified-diff-style hunk list, so identical logical content
  always diffs to zero hunks (replayable, D-36-02).
- **Rollback as a new child (T-36-03-02):** rollback restores the target
  revision's content into the chapter head and appends a **new** ``rollback``
  row whose parent is the current head; the actor, reason and approval state
  are journaled. No historical row is touched.
- **Owner isolation:** every query is scoped by owner + novel + project +
  chapter; a foreign/missing id is an identical 404.

Per D-36-03 every write only forms a **Fanfiction Canon draft**: the service
never writes Original Canon or User Interpretation and exposes no release
surface (Phase 39 owns release via the immutable revision service).

拆分说明（refactor split）：CAS 写入路径（``autosave_revision`` /
``apply_agent_edit`` / ``rollback_revision``）、owner 作用域加载与行追加原语
保留在本门面；确定性 diff（``diff_markdown``）拆到 ``_diff.py``，视图构建器
（``to_*_view``）拆到 ``_views.py``，agent-edit proposal 原语（payload/hash/
错误/结果载体）拆到 ``_agent_edit.py``。本模块显式 re-export 全部同名符号，
``from app.services.derivative_editor.revisions import X`` 的 import surface
不变。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_runtime import ApprovalRequest
from app.models.derivative_chapter import DerivativeChapter
from app.models.derivative_project import DerivativeProject
from app.models.derivative_revision import (
    DERIVATIVE_REVISION_APPROVAL_STATES,
    DERIVATIVE_REVISION_KINDS,
    DerivativeRevision,
)
from app.schemas.derivative_chapter import DerivativeChapterView
from app.schemas.derivative_revision import DerivativeRevisionView
from app.services.derivative_editor.chapters import (
    canonicalize_markdown,
    markdown_checksum,
)

# ────────────────────────── 拆分后 leaf 模块 re-export ──────────────────────────
from ._agent_edit import (
    DERIVATIVE_AGENT_EDIT_APPROVAL_PREFIX,
    DERIVATIVE_AGENT_EDIT_APPROVAL_SCHEMA_VERSION,
    AgentEditProposalResult,
    DerivativeEditApplyError,
    build_derivative_edit_approval_payload,
    canonical_derivative_edit_approval_hash,
    derivative_edit_content_hash,
)
from ._diff import _split_lines, diff_markdown  # noqa: F401  (parity: was module-level)
from ._views import to_chapter_view, to_revision_summary, to_revision_view


class DerivativeRevisionError(ValueError):
    """Fail-closed revision gate violation with an HTTP status code.

    ``current_revision`` carries the head revision row for a 409 conflict so the
    API can return the latest revision to the stale client (recoverable, never
    a blind rejection).
    """

    def __init__(
        self,
        code: str,
        detail: str,
        status_code: int = 400,
        current_revision: DerivativeRevision | None = None,
    ):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.current_revision = current_revision
        super().__init__(f"{code}: {detail}")


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise DerivativeRevisionError(
            "invalid_scope", "scope identifiers must be explicit positive integers"
        )


# ---------------------------------------------------------------------------
# Owner-scoped loads (a foreign/missing id is an identical 404)
# ---------------------------------------------------------------------------


async def _load_scoped_project(
    db: AsyncSession, *, owner_id: int, novel_id: int, project_id: int
) -> DerivativeProject:
    row = await db.scalar(
        select(DerivativeProject).where(
            DerivativeProject.id == project_id,
            DerivativeProject.owner_id == owner_id,
            DerivativeProject.novel_id == novel_id,
        )
    )
    if row is None:
        raise DerivativeRevisionError(
            "project_not_found",
            "derivative project not found in the owner/novel scope",
            status_code=404,
        )
    return row


async def _load_scoped_chapter(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
) -> DerivativeChapter:
    row = await db.scalar(
        select(DerivativeChapter).where(
            DerivativeChapter.id == chapter_id,
            DerivativeChapter.project_id == project_id,
            DerivativeChapter.owner_id == owner_id,
            DerivativeChapter.novel_id == novel_id,
        )
    )
    if row is None:
        raise DerivativeRevisionError(
            "chapter_not_found",
            "derivative chapter not found in the owner/novel/project scope",
            status_code=404,
        )
    return row


async def _load_scoped_revision(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    revision_id: int,
) -> DerivativeRevision:
    row = await db.scalar(
        select(DerivativeRevision).where(
            DerivativeRevision.id == revision_id,
            DerivativeRevision.owner_id == owner_id,
            DerivativeRevision.novel_id == novel_id,
            DerivativeRevision.project_id == project_id,
            DerivativeRevision.chapter_id == chapter_id,
        )
    )
    if row is None:
        raise DerivativeRevisionError(
            "revision_not_found",
            "revision not found in the owner/novel/project/chapter scope",
            status_code=404,
        )
    return row


async def _latest_revision_row(
    db: AsyncSession, *, chapter_id: int, owner_id: int
) -> DerivativeRevision | None:
    return await db.scalar(
        select(DerivativeRevision)
        .where(
            DerivativeRevision.chapter_id == chapter_id,
            DerivativeRevision.owner_id == owner_id,
        )
        .order_by(
            DerivativeRevision.revision_number.desc(),
            DerivativeRevision.id.desc(),
        )
        .limit(1)
    )


def _require_writable_project(project: DerivativeProject) -> None:
    """Archived projects are read-only for revision writes (soft close)."""
    if project.status == "archived":
        raise DerivativeRevisionError(
            "project_archived",
            f"project {project.id} is archived; revision writes are blocked",
            status_code=409,
        )


async def append_revision_row(
    db: AsyncSession,
    *,
    chapter: DerivativeChapter,
    revision_number: int,
    kind: str,
    content: str,
    checksum: str,
    actor_id: int,
    reason: str | None = None,
    approval_state: str = "not_required",
) -> DerivativeRevision:
    """Append one immutable revision row; existing rows are never touched.

    ``parent_revision_id`` always points at the current head row of the same
    chapter (the last appended row), so the lineage chain stays complete even
    when plan-field patches and autosaves interleave.
    """
    if kind not in DERIVATIVE_REVISION_KINDS:
        raise DerivativeRevisionError(
            "invalid_revision_kind", f"unknown revision kind {kind!r}"
        )
    if approval_state not in DERIVATIVE_REVISION_APPROVAL_STATES:
        raise DerivativeRevisionError(
            "invalid_approval_state", f"unknown approval state {approval_state!r}"
        )
    latest = await _latest_revision_row(
        db, chapter_id=chapter.id, owner_id=chapter.owner_id
    )
    row = DerivativeRevision(
        chapter_id=chapter.id,
        owner_id=chapter.owner_id,
        novel_id=chapter.novel_id,
        project_id=chapter.project_id,
        revision_number=revision_number,
        parent_revision_id=latest.id if latest is not None else None,
        kind=kind,
        content=content,
        content_checksum=checksum,
        actor_id=actor_id,
        reason=reason,
        approval_state=approval_state,
    )
    db.add(row)
    await db.flush()
    return row


# ---------------------------------------------------------------------------
# Autosave: conditional CAS, idempotent replay, never last-write-wins
# ---------------------------------------------------------------------------


async def autosave_revision(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    content: str,
    base_revision: int,
    actor_id: int,
) -> tuple[DerivativeChapterView, DerivativeRevisionView, str]:
    """Draft autosave guarded by an atomic conditional update (D-36-02).

    Returns ``(chapter view, revision view, status)`` where status is:

    - ``saved``: a new immutable row was appended and the chapter advanced;
    - ``noop``: the submitted canonical content already equals the head — a
      same-base no-op or a crash-before-ack retry resolves idempotently with no
      new row and no revision bump.

    A stale/conflicting base raises 409 carrying the current head revision.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    _require_writable_project(project)
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        chapter_id=chapter_id,
    )

    canonical = canonicalize_markdown(content)
    checksum = markdown_checksum(canonical)

    # No semantic change: a duplicate autosave or a crash-before-ack retry of
    # the already-committed head both resolve to an idempotent success (no new
    # row, no revision bump).
    if checksum == chapter.markdown_checksum:
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        return to_chapter_view(chapter), to_revision_view(latest), "noop"

    if base_revision != chapter.revision:
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        raise DerivativeRevisionError(
            "revision_conflict",
            f"stale write: chapter {chapter.id} is at revision {chapter.revision} "
            f"with checksum {chapter.markdown_checksum}; client sent "
            f"base_revision {base_revision}",
            status_code=409,
            current_revision=latest,
        )

    # Atomic conditional update: only the row still at `base_revision` matches,
    # so a concurrent writer can never be silently overwritten (no LWW). The
    # losing writer's UPDATE blocks and then re-evaluates the WHERE on the
    # committed row — it matches 0 rows and fails closed below.
    result = await db.execute(
        update(DerivativeChapter)
        .where(
            DerivativeChapter.id == chapter.id,
            DerivativeChapter.revision == base_revision,
        )
        .values(
            markdown=canonical,
            markdown_checksum=checksum,
            revision=DerivativeChapter.revision + 1,
            updated_at=func.now(),
        )
    )
    if result.rowcount == 0:
        # A concurrent writer committed between our read and the conditional
        # update; reload the head and re-evaluate (replay vs conflict).
        await db.refresh(chapter)
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        if checksum == chapter.markdown_checksum:
            return to_chapter_view(chapter), to_revision_view(latest), "noop"
        raise DerivativeRevisionError(
            "revision_conflict",
            f"stale write: chapter {chapter.id} is at revision {chapter.revision} "
            f"with checksum {chapter.markdown_checksum}; client sent "
            f"base_revision {base_revision}",
            status_code=409,
            current_revision=latest,
        )

    await db.refresh(chapter)
    revision = await append_revision_row(
        db,
        chapter=chapter,
        revision_number=chapter.revision,
        kind="autosave",
        content=canonical,
        checksum=checksum,
        actor_id=actor_id,
    )
    return to_chapter_view(chapter), to_revision_view(revision), "saved"


# ---------------------------------------------------------------------------
# Agent-proposal path (36-05, D-36-02): candidate proposal gate + deterministic
# Revision Service apply. This is the server-authoritative consumer of the
# edit-derivative-story Skill. user_autosave and agent_proposal stay disjoint:
# separate endpoints, event names, actor labels and CAS entry points; a user
# autosave never satisfies an apply_derivative_edit ApprovalRequest and the
# Agent/browser can never apply a proposal directly.
# ---------------------------------------------------------------------------


# The only approval action that may reach the deterministic Revision Service.
DERIVATIVE_AGENT_EDIT_APPROVAL_ACTION = "apply_derivative_edit"


async def create_agent_edit_proposal(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    content: str,
    base_revision: int,
    proposal_key: str,
    branch: str | None,
    fork: str | None,
    source_snapshot_hash: str | None,
    run_id: int | None = None,
    skill_version_id: int | None = None,
    artifact_id: int | None = None,
    artifact_revision_id: int | None = None,
) -> AgentEditProposalResult:
    """Server-authoritative candidate proposal creation (D-36-02 / D-11 / D-15).

    - Scope-checks the project (owner/novel + ``fanfiction_canon`` space) and
      the chapter (project scope); a foreign/missing id is an identical 404.
    - Verifies the supplied source snapshot hash replays the project's frozen
      fork lineage (wrong branch/fork fails closed).
    - Computes the deterministic content hash and creates the **pending** Web
      ApprovalRequest (action = ``apply_derivative_edit``) bound to the frozen
      approval payload hash.
    Nothing here applies; the deterministic Revision Service
    (``apply_agent_edit``) owns approved proposal application after the user
    confirms the approval.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    if base_revision <= 0:
        raise DerivativeEditApplyError(
            "invalid_base_revision",
            "base_revision must be a positive optimistic-concurrency token",
        )
    if not proposal_key.strip():
        raise DerivativeEditApplyError(
            "invalid_proposal_key", "proposal_key must be non-empty"
        )
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    _require_writable_project(project)
    if project.space != "fanfiction_canon":
        raise DerivativeEditApplyError(
            "wrong_authority_space",
            f"derivative project {project.id} is in space {project.space!r}; "
            "only fanfiction_canon projects accept derivative edits",
        )
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )
    if (
        source_snapshot_hash is not None
        and source_snapshot_hash != project.source_snapshot_hash
    ):
        raise DerivativeEditApplyError(
            "source_snapshot_mismatch",
            "proposal source snapshot hash does not replay the project's frozen "
            "fork lineage (wrong branch/fork fails closed)",
        )

    canonical = canonicalize_markdown(content)
    checksum = markdown_checksum(canonical)
    payload = build_derivative_edit_approval_payload(
        owner_id=owner_id,
        novel_id=novel_id,
        branch=branch,
        fork=fork,
        proposal_key=proposal_key,
        project_id=project.id,
        chapter_id=chapter.id,
        base_revision=base_revision,
        content_hash=checksum,
        source_snapshot_hash=project.source_snapshot_hash,
    )
    payload_hash = canonical_derivative_edit_approval_hash(payload)

    existing = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.action == DERIVATIVE_AGENT_EDIT_APPROVAL_ACTION,
            ApprovalRequest.owner_id == owner_id,
            ApprovalRequest.fork_id == project.fork_id,
        )
    )
    if existing is not None:
        if existing.payload_hash == payload_hash:
            return AgentEditProposalResult(
                owner_id=owner_id,
                novel_id=novel_id,
                project_id=project.id,
                chapter_id=chapter.id,
                approval_request_id=existing.id,
                approval_action=existing.action,
                approval_status=existing.status,
                approval_payload_hash=existing.payload_hash,
                content_hash=checksum,
                proposal_key=proposal_key,
                base_revision=base_revision,
                replayed=True,
            )
        raise DerivativeEditApplyError(
            "proposal_conflict",
            "this project already has a different derivative-edit proposal "
            "pending; replay the existing proposal instead of widening the scope",
        )

    approval = ApprovalRequest(
        owner_id=owner_id,
        run_id=run_id,
        skill_version_id=skill_version_id,
        artifact_id=artifact_id,
        artifact_revision_id=artifact_revision_id,
        novel_id=novel_id,
        branch_id=None,
        fork_id=project.fork_id,
        action=DERIVATIVE_AGENT_EDIT_APPROVAL_ACTION,
        payload_summary={
            "proposal_key": proposal_key,
            "project_id": project.id,
            "chapter_id": chapter.id,
            "base_revision": base_revision,
            "content_hash": checksum,
            "branch": branch,
            "fork": fork,
            "source_snapshot_hash": project.source_snapshot_hash,
        },
        payload_hash=payload_hash,
        status="pending",
        expires_at=None,
    )
    db.add(approval)
    await db.flush()
    return AgentEditProposalResult(
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter.id,
        approval_request_id=approval.id,
        approval_action=DERIVATIVE_AGENT_EDIT_APPROVAL_ACTION,
        approval_status="pending",
        approval_payload_hash=payload_hash,
        content_hash=checksum,
        proposal_key=proposal_key,
        base_revision=base_revision,
        replayed=False,
    )


async def apply_agent_edit(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    content: str,
    base_revision: int,
    actor_id: int,
    reason: str | None = None,
) -> tuple[DerivativeChapterView, DerivativeRevisionView, str]:
    """Deterministic Revision Service apply of an approved agent proposal.

    This is the **only** write path an approved ``apply_derivative_edit``
    proposal may take: it reuses the same immutable ``base_revision`` CAS as the
    user autosave (no last-write-wins) but appends a distinct immutable
    ``agent_proposal`` revision row (``approval_state=approved``, the proposal
    key journaled as ``reason``), so an agent proposal can never be mistaken for
    a user draft and the two event/actor/CAS paths stay auditable.

    Returns ``(chapter view, revision view, status)`` with status ``applied``
    (a new row was appended) or ``noop`` (the approved content already equals
    the head — an idempotent replay of the same approved proposal).
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    _require_writable_project(project)
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )

    canonical = canonicalize_markdown(content)
    checksum = markdown_checksum(canonical)

    # Idempotent replay: an identical approved proposal whose content already
    # equals the head resolves without a new row (no duplicate history).
    if checksum == chapter.markdown_checksum:
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        return to_chapter_view(chapter), to_revision_view(latest), "noop"

    if base_revision != chapter.revision:
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        raise DerivativeRevisionError(
            "revision_conflict",
            f"stale write: chapter {chapter.id} is at revision {chapter.revision} "
            f"with checksum {chapter.markdown_checksum}; the approved proposal "
            f"targets base_revision {base_revision}",
            status_code=409,
            current_revision=latest,
        )

    # Atomic conditional update keyed on base_revision: a concurrent autosave
    # or proposal can never be silently overwritten (no last-write-wins).
    result = await db.execute(
        update(DerivativeChapter)
        .where(
            DerivativeChapter.id == chapter.id,
            DerivativeChapter.revision == base_revision,
        )
        .values(
            markdown=canonical,
            markdown_checksum=checksum,
            revision=DerivativeChapter.revision + 1,
            updated_at=func.now(),
        )
    )
    if result.rowcount == 0:
        await db.refresh(chapter)
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        if checksum == chapter.markdown_checksum:
            return to_chapter_view(chapter), to_revision_view(latest), "noop"
        raise DerivativeRevisionError(
            "revision_conflict",
            f"stale write: chapter {chapter.id} is at revision {chapter.revision} "
            f"with checksum {chapter.markdown_checksum}; the approved proposal "
            f"targets base_revision {base_revision}",
            status_code=409,
            current_revision=latest,
        )

    await db.refresh(chapter)
    revision = await append_revision_row(
        db,
        chapter=chapter,
        revision_number=chapter.revision,
        kind="agent_proposal",
        content=canonical,
        checksum=checksum,
        actor_id=actor_id,
        reason=(reason or "").strip() or None,
        approval_state="approved",
    )
    return to_chapter_view(chapter), to_revision_view(revision), "applied"


# ---------------------------------------------------------------------------
# History + single revision detail
# ---------------------------------------------------------------------------


async def list_revisions(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
) -> tuple[DerivativeChapterView, list[DerivativeRevision]]:
    """Newest-first append-only history of one chapter."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )
    rows = list(
        (
            await db.scalars(
                select(DerivativeRevision)
                .where(
                    DerivativeRevision.chapter_id == chapter.id,
                    DerivativeRevision.owner_id == owner_id,
                    DerivativeRevision.novel_id == novel_id,
                    DerivativeRevision.project_id == project.id,
                )
                .order_by(
                    DerivativeRevision.revision_number.desc(),
                    DerivativeRevision.id.desc(),
                )
            )
        ).all()
    )
    return to_chapter_view(chapter), rows


async def get_revision(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    revision_id: int,
) -> DerivativeRevision:
    """Read one immutable revision; a foreign/missing revision is an identical 404."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )
    return await _load_scoped_revision(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter.id,
        revision_id=revision_id,
    )


# ---------------------------------------------------------------------------
# Deterministic diff between two revision rows
# ---------------------------------------------------------------------------


async def diff_revisions(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    base_revision_id: int,
    target_revision_id: int,
) -> tuple[DerivativeRevision, DerivativeRevision, list[dict[str, Any]]]:
    """Canonical-Markdown diff from ``base`` to ``target`` revision row."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )
    base = await _load_scoped_revision(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter.id,
        revision_id=base_revision_id,
    )
    target = await _load_scoped_revision(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter.id,
        revision_id=target_revision_id,
    )
    return base, target, diff_markdown(base.content, target.content)


# ---------------------------------------------------------------------------
# Rollback: a NEW child revision, never an in-place history rewrite
# ---------------------------------------------------------------------------


async def rollback_revision(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    target_revision_id: int,
    reason: str | None,
    base_revision: int,
    actor_id: int,
) -> tuple[DerivativeChapterView, DerivativeRevisionView]:
    """Restore a target revision's content as a NEW child (T-36-03-02).

    The rollback writes the target's canonical Markdown into the chapter head
    (CAS-guarded exactly like an autosave) and appends an immutable ``rollback``
    row whose parent is the current head, journaling the actor, reason and
    approval state. Historical rows are never modified or deleted, so the
    rollback itself remains a recoverable, auditable event.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    _require_writable_project(project)
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    target = await _load_scoped_revision(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter.id,
        revision_id=target_revision_id,
    )

    if base_revision != chapter.revision:
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        raise DerivativeRevisionError(
            "revision_conflict",
            f"stale write: chapter {chapter.id} is at revision {chapter.revision} "
            f"with checksum {chapter.markdown_checksum}; client sent "
            f"base_revision {base_revision}",
            status_code=409,
            current_revision=latest,
        )

    # Conditional update: never clobber a concurrent write while restoring.
    result = await db.execute(
        update(DerivativeChapter)
        .where(
            DerivativeChapter.id == chapter.id,
            DerivativeChapter.revision == base_revision,
        )
        .values(
            markdown=target.content,
            markdown_checksum=target.content_checksum,
            revision=DerivativeChapter.revision + 1,
            updated_at=func.now(),
        )
    )
    if result.rowcount == 0:
        await db.refresh(chapter)
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        raise DerivativeRevisionError(
            "revision_conflict",
            f"stale write: chapter {chapter.id} is at revision {chapter.revision} "
            f"with checksum {chapter.markdown_checksum}; client sent "
            f"base_revision {base_revision}",
            status_code=409,
            current_revision=latest,
        )

    await db.refresh(chapter)
    revision = await append_revision_row(
        db,
        chapter=chapter,
        revision_number=chapter.revision,
        kind="rollback",
        content=target.content,
        checksum=target.content_checksum,
        actor_id=actor_id,
        reason=(reason or "").strip() or None,
        approval_state="approved",
    )
    return to_chapter_view(chapter), to_revision_view(revision)


__all__ = [
    "DERIVATIVE_AGENT_EDIT_APPROVAL_ACTION",
    "DERIVATIVE_AGENT_EDIT_APPROVAL_PREFIX",
    "DERIVATIVE_AGENT_EDIT_APPROVAL_SCHEMA_VERSION",
    "AgentEditProposalResult",
    "DerivativeEditApplyError",
    "DerivativeRevisionError",
    "append_revision_row",
    "apply_agent_edit",
    "autosave_revision",
    "build_derivative_edit_approval_payload",
    "canonical_derivative_edit_approval_hash",
    "create_agent_edit_proposal",
    "derivative_edit_content_hash",
    "diff_markdown",
    "diff_revisions",
    "get_revision",
    "list_revisions",
    "rollback_revision",
    "to_chapter_view",
    "to_revision_summary",
    "to_revision_view",
]
