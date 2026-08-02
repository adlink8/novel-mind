"""Deterministic policy and hashing for pre-generation context packages.

No function in this module invokes a provider, writes a database row, changes
an active pointer, or changes a Reader Chat consumer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.schemas.creative_generation import CreativeContextPackage


class CreativeContextPolicyError(ValueError):
    """Machine-readable rejection for an unsafe or out-of-scope package."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _canonical_payload(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def context_hash(package: CreativeContextPackage) -> str:
    """Return the hash of package content excluding its self-describing hash."""

    payload = package.model_dump(mode="json", exclude={"context_hash"})
    return hashlib.sha256(_canonical_payload(payload)).hexdigest()


def build_context_package(
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    cutoff_chapter_number: int,
    user_settings: dict[str, Any] | None = None,
    original_evidence: list[dict[str, Any]] | None = None,
    understanding_states: list[dict[str, Any]] | None = None,
    override: dict[str, Any] | None = None,
) -> CreativeContextPackage:
    """Build and hash a package without contacting any external system."""

    data: dict[str, Any] = {
        "owner_id": owner_id,
        "novel_id": novel_id,
        "project_id": project_id,
        "cutoff_chapter_number": cutoff_chapter_number,
        "user_settings": user_settings or {},
        "original_evidence": original_evidence or [],
        "understanding_states": understanding_states or [],
        "override": override,
        "candidate_only": True,
    }
    provisional = CreativeContextPackage(**data, context_hash="0" * 64)
    return provisional.model_copy(update={"context_hash": context_hash(provisional)})


def validate_context_package(
    package: CreativeContextPackage, *, owner_id: int, novel_id: int
) -> CreativeContextPackage:
    """Validate caller scope, lineage hash, and non-promoting output policy."""

    if package.owner_id != owner_id:
        raise CreativeContextPolicyError("owner_scope", "context package is outside the owner scope")
    if package.novel_id != novel_id:
        raise CreativeContextPolicyError("novel_scope", "context package is outside the novel scope")
    if package.output_space != "fanfiction_canon" or package.candidate_only is not True:
        raise CreativeContextPolicyError(
            "candidate_only_required",
            "creative generation preparation must remain Fanfiction Canon candidate-only",
        )
    if context_hash(package) != package.context_hash:
        raise CreativeContextPolicyError("context_hash", "context package hash does not match its content")
    return package
