"""Phase 36-01 strict wire contracts for the derivative project CRUD (D-36-01/03).

The client can never widen the owner/novel/space/version scope: create requests
carry only the explicit ``fork_id`` selection plus mutable display fields, and
every view echoes the frozen fork lineage (scope + version) so all responses
are auditable on the wire.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.derivative_project import DERIVATIVE_PROJECT_SPACE

# Stripped, non-empty name and identity constraints (mirrors canon-fork contracts).
ProjectNameStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]
ProjectKeyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class StrictDerivativeProjectModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class DerivativeProjectCreate(StrictDerivativeProjectModel):
    """Client intent: explicit Canon Fork selection + mutable display fields.

    owner/novel, the fork lineage, the space and the version/cutoff are all
    derived and sealed by the server from the owned fork (D-36-01/D-36-03).
    """

    fork_id: int = Field(gt=0)
    name: ProjectNameStr
    project_key: ProjectKeyStr | None = None
    description: str | None = Field(default=None, max_length=2000)


class DerivativeProjectPatch(StrictDerivativeProjectModel):
    """Mutable project state only; the fork lineage can never be patched."""

    name: ProjectNameStr | None = None
    description: str | None = Field(default=None, max_length=2000)
    status: DerivativeProjectStatus | None = None

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> DerivativeProjectPatch:
        if self.name is None and self.description is None and self.status is None:
            raise ValueError("at least one of name, description or status is required")
        return self


class DerivativeProjectView(StrictDerivativeProjectModel):
    """Owner-scoped project with the frozen fork scope/version lineage echoed."""

    id: int
    owner_id: int
    novel_id: int
    fork_id: int
    project_key: str
    name: str
    description: str | None = None
    status: DerivativeProjectStatus
    space: str = DERIVATIVE_PROJECT_SPACE
    fork_key: str
    source_version_key: str
    source_snapshot_hash: str
    through_chapter: int
    full_book_authorized: bool
    cutoff_snapshot_hash: str
    scope_hash: str
    manifest_hash: str
    created_at: datetime
    updated_at: datetime


class DerivativeProjectCreateResponse(StrictDerivativeProjectModel):
    project: DerivativeProjectView
    message: str | None = None


class DerivativeProjectListResponse(StrictDerivativeProjectModel):
    novel_id: int
    total: int
    items: list[DerivativeProjectView] = Field(default_factory=list)
