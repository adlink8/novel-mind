"""Structured, deterministic consistency-gate contracts for creative drafts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.creative_generation import StrictCreativeModel


class CreativeClaim(StrictCreativeModel):
    claim_key: str = Field(min_length=1, max_length=160)
    category: Literal["character_behavior", "established_fact", "timeline"]
    text: str = Field(min_length=1, max_length=4000)
    evidence_keys: list[str] = Field(default_factory=list, max_length=8)
    chapter_number: int | None = Field(default=None, ge=1)
    disposition: Literal["consistent", "contradiction", "unknown"] = "unknown"


class CreativeConsistencyFinding(StrictCreativeModel):
    claim_key: str
    rule_code: Literal[
        "missing_evidence",
        "evidence_outside_package",
        "cutoff_exceeded",
        "contradiction",
        "uncertain",
    ]
    severity: Literal["error", "warning"]
    detail: str


class CreativeConsistencyReport(StrictCreativeModel):
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_claims: int = Field(ge=0)
    citation_coverage: float = Field(ge=0, le=1)
    status: Literal["passed", "passed_with_warnings", "failed"]
    findings: list[CreativeConsistencyFinding] = Field(
        default_factory=list, max_length=256
    )
    candidate_only: Literal[True] = True
    report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
