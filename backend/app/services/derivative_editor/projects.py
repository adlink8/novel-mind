"""Owner-scoped derivative project CRUD (Phase 36-01, D-36-01/D-36-03).

The service is the only owner-scoped transaction boundary for projects:

- **Explicit fork selection (D-36-01):** creation always requires ``fork_id``.
  The fork is loaded within the owner/novel scope; a fork outside the scope is
  an identical 404, a fork in any space other than ``fanfiction_canon`` fails
  closed (403), and a rejected/archived fork cannot anchor a project (409).
  The fork's frozen version/cutoff/hash lineage is copied into the project row
  so the project carries its own auditable scope snapshot.
- **Fanfiction-only writes (D-36-03):** no create/update path can ever write
  Original Canon or User Interpretation; ``space`` is always sealed to
  ``fanfiction_canon`` by the service and the database CheckConstraint.
- **No implicit reading-page inference:** the reading progress of the Novel is
  never consulted; the owner must always state the fork explicitly.
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canon_fork import CANON_FORK_SPACE, CanonFork
from app.models.derivative_project import (
    DERIVATIVE_PROJECT_SPACE,
    DERIVATIVE_PROJECT_USABLE_FORK_STATUSES,
    DerivativeProject,
)
from app.schemas.derivative_project import (
    DerivativeProjectPatch,
    DerivativeProjectStatus,
    DerivativeProjectView,
)


class DerivativeProjectError(ValueError):
    """Fail-closed project gate violation with an HTTP status code."""

    def __init__(self, code: str, detail: str, status_code: int = 400):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail}")


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise DerivativeProjectError(
            "invalid_scope", "scope identifiers must be explicit positive integers"
        )


def slugify_project_key(name: str) -> str:
    """Deterministic ASCII slug for the project identity; never empty."""
    value = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if value:
        return value[:120]
    digest = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()
    return f"project-{digest[:16]}"


async def _load_scoped_fork(
    db: AsyncSession, *, owner_id: int, novel_id: int, fork_id: int
) -> CanonFork:
    """Load a fork within the owner/novel scope; a foreign fork is an identical 404."""
    fork = await db.scalar(
        select(CanonFork).where(
            CanonFork.id == fork_id,
            CanonFork.owner_id == owner_id,
            CanonFork.novel_id == novel_id,
        )
    )
    if fork is None:
        raise DerivativeProjectError(
            "fork_not_found",
            "canon fork not found in the owner/novel scope",
            status_code=404,
        )
    if fork.space != CANON_FORK_SPACE:
        raise DerivativeProjectError(
            "fork_space_denied",
            f"only {DERIVATIVE_PROJECT_SPACE} forks can anchor a project",
            status_code=403,
        )
    if fork.status not in DERIVATIVE_PROJECT_USABLE_FORK_STATUSES:
        raise DerivativeProjectError(
            "fork_not_usable",
            f"fork {fork.id} is {fork.status!r}; rejected/archived forks cannot "
            "anchor a project",
            status_code=409,
        )
    return fork


async def _require_unique_name(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    name: str,
    exclude_id: int | None = None,
) -> None:
    query = select(DerivativeProject).where(
        DerivativeProject.owner_id == owner_id,
        DerivativeProject.novel_id == novel_id,
        DerivativeProject.name == name,
    )
    if exclude_id is not None:
        query = query.where(DerivativeProject.id != exclude_id)
    existing = await db.scalar(query)
    if existing is not None:
        raise DerivativeProjectError(
            "name_conflict",
            f"a derivative project named {name!r} already exists in this novel",
            status_code=409,
        )


def _to_view(row: DerivativeProject) -> DerivativeProjectView:
    return DerivativeProjectView(
        id=row.id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        fork_id=row.fork_id,
        project_key=row.project_key,
        name=row.name,
        description=row.description,
        status=DerivativeProjectStatus(row.status),
        space=row.space,
        fork_key=row.fork_key,
        source_version_key=row.source_version_key,
        source_snapshot_hash=row.source_snapshot_hash,
        through_chapter=row.through_chapter,
        full_book_authorized=bool(row.full_book_authorized),
        cutoff_snapshot_hash=row.cutoff_snapshot_hash,
        scope_hash=row.scope_hash,
        manifest_hash=row.manifest_hash,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def create_project(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    fork_id: int,
    name: str,
    project_key: str | None = None,
    description: str | None = None,
) -> DerivativeProjectView:
    """Create an owner-scoped project bound to an explicit Fanfiction Canon Fork.

    The client never supplies owner/novel/space/version/cutoff: they are derived
    and sealed from the owned fork. The project cannot exist without a fork.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    name = (name or "").strip()
    if not name:
        raise DerivativeProjectError("invalid_name", "project name must be non-empty")

    fork = await _load_scoped_fork(
        db, owner_id=owner_id, novel_id=novel_id, fork_id=fork_id
    )
    await _require_unique_name(db, owner_id=owner_id, novel_id=novel_id, name=name)

    key = (project_key or "").strip() or slugify_project_key(name)
    row = DerivativeProject(
        owner_id=owner_id,
        novel_id=novel_id,
        fork_id=fork.id,
        project_key=key,
        name=name,
        description=(description or "").strip() or None,
        status=DerivativeProjectStatus.ACTIVE.value,
        # Frozen Fanfiction Canon lineage copied from the chosen fork.
        space=DERIVATIVE_PROJECT_SPACE,
        fork_key=fork.fork_key,
        source_version_key=fork.source_version_key,
        source_snapshot_hash=fork.source_snapshot_hash,
        through_chapter=fork.through_chapter,
        full_book_authorized=bool(fork.full_book_authorized),
        cutoff_snapshot_hash=fork.cutoff_snapshot_hash,
        scope_hash=fork.scope_hash,
        manifest_hash=fork.manifest_hash,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise DerivativeProjectError(
            "project_key_conflict",
            f"project_key {key!r} is already in use for this novel",
            status_code=409,
        ) from exc
    await db.refresh(row)
    return _to_view(row)


async def list_projects(
    db: AsyncSession, *, owner_id: int, novel_id: int
) -> list[DerivativeProjectView]:
    """List the owner's projects for one novel (no implicit active pick)."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    rows = list(
        (
            await db.scalars(
                select(DerivativeProject)
                .where(
                    DerivativeProject.owner_id == owner_id,
                    DerivativeProject.novel_id == novel_id,
                )
                .order_by(DerivativeProject.id.desc())
            )
        ).all()
    )
    return [_to_view(row) for row in rows]


async def get_project(
    db: AsyncSession, *, owner_id: int, novel_id: int, project_id: int
) -> DerivativeProjectView:
    """Read one project; a foreign/missing project is an identical 404."""
    row = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    return _to_view(row)


async def update_project(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    patch: DerivativeProjectPatch,
) -> DerivativeProjectView:
    """Patch mutable project state; the frozen fork lineage never changes."""
    row = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    if patch.name is not None:
        new_name = patch.name.strip()
        if not new_name:
            raise DerivativeProjectError(
                "invalid_name", "project name must be non-empty"
            )
        await _require_unique_name(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            name=new_name,
            exclude_id=row.id,
        )
        row.name = new_name
    if patch.description is not None:
        row.description = patch.description.strip() or None
    if patch.status is not None:
        row.status = patch.status.value
    await db.flush()
    await db.refresh(row)
    return _to_view(row)


async def delete_project(
    db: AsyncSession, *, owner_id: int, novel_id: int, project_id: int
) -> None:
    """Hard-delete one owner-scoped project (archiving is the soft option)."""
    row = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    await db.delete(row)
    await db.flush()


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
        raise DerivativeProjectError(
            "project_not_found",
            "derivative project not found in the owner/novel scope",
            status_code=404,
        )
    return row


__all__ = [
    "DERIVATIVE_PROJECT_SPACE",
    "DerivativeProjectError",
    "create_project",
    "delete_project",
    "get_project",
    "list_projects",
    "slugify_project_key",
    "update_project",
]
