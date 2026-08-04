"""Phase 37-01 strict wire contracts for the derivative context package (D-37-01).

The client can never widen the owner/novel/version/cutoff scope: compile
requests carry only the explicit ``fork_id`` selection, the generation
``intent`` and an optional narrower ``through_chapter`` (never beyond the
fork's frozen cutoff). Every view echoes the server-sealed fork lineage and
``package_hash`` so the frozen context stays auditable on the wire.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.derivative_context import DERIVATIVE_CONTEXT_SPACE

# Stripped, non-empty package identity constraints (mirrors canon-fork contracts).
PackageKeyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class StrictDerivativeContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeContextIntent(StrEnum):
    CONTINUATION = "continuation"
    REWRITE = "rewrite"


class ContextPackageCreateRequest(StrictDerivativeContextModel):
    """Client intent: explicit Canon Fork selection + generation intent.

    owner/novel, the fork lineage, the space and the effective cutoff are all
    derived and sealed by the server from the owned fork (D-37-01); a requested
    ``through_chapter`` can only shrink, never expand, the fork scope.
    """

    fork_id: int = Field(gt=0)
    intent: DerivativeContextIntent
    through_chapter: int | None = Field(default=None, gt=0)


class ContextPackageView(StrictDerivativeContextModel):
    """Sealed package row with the frozen fork lineage and the compiled payload."""

    id: int
    owner_id: int
    novel_id: int
    fork_id: int
    package_key: str
    space: str = DERIVATIVE_CONTEXT_SPACE
    intent: DerivativeContextIntent
    fork_key: str
    source_version_key: str
    source_snapshot_hash: str
    through_chapter: int
    full_book_authorized: bool
    cutoff_snapshot_hash: str
    scope_hash: str
    manifest_hash: str
    package_hash: str
    budget_estimate: dict = Field(default_factory=dict)
    payload: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ContextPackageSummary(StrictDerivativeContextModel):
    """Lean list row without the full compiled payload."""

    id: int
    owner_id: int
    novel_id: int
    fork_id: int
    package_key: str
    intent: DerivativeContextIntent
    fork_key: str
    through_chapter: int
    scope_hash: str
    package_hash: str
    created_at: datetime


class ContextPackageCreateResponse(StrictDerivativeContextModel):
    package: ContextPackageView
    replayed: bool = False
    message: str | None = None


class ContextPackageListResponse(StrictDerivativeContextModel):
    novel_id: int
    total: int
    items: list[ContextPackageSummary] = Field(default_factory=list)


__all__ = [
    "ContextPackageCreateRequest",
    "ContextPackageCreateResponse",
    "ContextPackageListResponse",
    "ContextPackageSummary",
    "ContextPackageView",
    "DerivativeContextIntent",
]
