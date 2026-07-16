"""Product-facing Narrative Memory structure contracts (read-only candidate preview).

These schemas intentionally omit production promotion / active-pointer fields.
Always surface ``candidate_preview`` badges for Structure Workspace.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


NmReadiness = Literal[
    "empty",
    "incomplete",
    "preview_eligible",
    "sealed_candidate",
]

NmPublicationStatus = Literal["candidate_preview"]
NmBadge = Literal["candidate_preview"]


class StrictNmProductModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NmVersionListItem(StrictNmProductModel):
    version_id: int = Field(gt=0)
    version_key: str = Field(min_length=1, max_length=120)
    readiness: NmReadiness
    badge: NmBadge = "candidate_preview"
    node_counts: dict[str, int] | None = None
    has_manifest: bool = False
    validation_verdict: str | None = None
    created_at: str | None = None


class NmVersionListResponse(StrictNmProductModel):
    novel_id: int = Field(gt=0)
    versions: list[NmVersionListItem]
    publication_status: NmPublicationStatus = "candidate_preview"
    message: str | None = None


class NmStructureNode(StrictNmProductModel):
    id: int = Field(gt=0)
    node_key: str = Field(min_length=1, max_length=160)
    node_kind: str = Field(min_length=1, max_length=32)
    display_label: str | None = None
    chapter_start: int = Field(gt=0)
    chapter_end: int = Field(gt=0)
    child_ids: list[int] = Field(default_factory=list)


class NmStructureTreeResponse(StrictNmProductModel):
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    through_chapter: int = Field(gt=0)
    publication_status: NmPublicationStatus = "candidate_preview"
    readiness: NmReadiness
    nodes: list[NmStructureNode] = Field(default_factory=list)
    message: str | None = None


class NmClaimItem(StrictNmProductModel):
    id: int = Field(gt=0)
    claim_kind: str = Field(min_length=1, max_length=40)
    summary: str = ""
    text: str | None = None
    typed_payload: dict[str, Any] = Field(default_factory=dict)
    uncertainty: str = Field(min_length=1, max_length=24)
    confidence: float = Field(ge=0.0, le=1.0)
    visible_from_chapter: int = Field(gt=0)
    node_id: int = Field(gt=0)


class NmClaimsResponse(StrictNmProductModel):
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    node_id: int = Field(gt=0)
    through_chapter: int = Field(gt=0)
    publication_status: NmPublicationStatus = "candidate_preview"
    claims: list[NmClaimItem] = Field(default_factory=list)
    message: str | None = None


class NmSourceLinkItem(StrictNmProductModel):
    id: int = Field(gt=0)
    claim_id: int = Field(gt=0)
    source_kind: str = Field(min_length=1, max_length=24)
    hierarchy_build_id: str = Field(min_length=1, max_length=64)
    evidence_node_id: str = Field(min_length=1, max_length=64)
    chapter_number: int = Field(gt=0)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    content_hash: str | None = None
    optional_source_ref: dict[str, Any] | None = None


class NmSourceLinksResponse(StrictNmProductModel):
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    node_id: int = Field(gt=0)
    through_chapter: int = Field(gt=0)
    publication_status: NmPublicationStatus = "candidate_preview"
    source_links: list[NmSourceLinkItem] = Field(default_factory=list)
    message: str | None = None
