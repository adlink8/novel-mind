"""产物与不可变修订行 wire 模型（D-10 血缘字段）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.agent_runtime.base import StrictAgentRuntimeModel


class ArtifactView(StrictAgentRuntimeModel):
    """产物行（D-10 血缘字段）。"""

    id: int
    owner_id: int
    novel_id: int
    skill_version_id: int
    run_id: int
    branch: str | None = None
    type: str
    schema_version: str
    status: Literal["candidate", "validated", "approved", "published", "rejected"]
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    source_versions: dict[str, Any] = Field(default_factory=dict)
    input_hash: str
    current_revision_id: int | None = None
    created_at: datetime
    updated_at: datetime


class ArtifactRevisionView(StrictAgentRuntimeModel):
    """不可变产物修订行。"""

    id: int
    artifact_id: int
    owner_id: int
    novel_id: int
    revision_no: int
    content_hash: str
    parent_revision_id: int | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
