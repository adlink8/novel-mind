"""Immutable PublishedDerivativeRevision DTO (Phase 37-04, Phase 39 consumer).

D-37-03 / D-37-04: when an owner explicitly approves a divergence override, the
deterministic override service materializes the candidate into a Fanfiction
Canon revision and emits an **immutable** ``PublishedDerivativeRevision`` — the
contract a later release phase (39) consumes. The DTO is a pure frozen value
object with no write surface; the cross-plan contract test freezes its exact
field set so a future phase cannot silently widen or rename the contract.

Field semantics:

- ``owner_id`` / ``project_id`` / ``fork_id`` — the owner scope and the target
  derivative project / Fanfiction Canon Fork lineage the revision lives in.
- ``revision_id`` — the immutable ``derivative_revisions`` row id.
- ``version_id`` — the chapter's optimistic-concurrency token the revision was
  appended at (``DerivativeChapter.revision``).
- ``status`` — the derivative-only publication status; it is always
  ``derivative_revision`` and never ``original`` / ``promoted`` (D-37-04: a
  generated revision never becomes a quality qualification or a production
  promotion; Phase 22 nightly qualification stays independent).
- ``source_snapshot`` — the frozen fork ``source_snapshot_hash`` the project is
  anchored to (byte-replayable lineage).
- ``manifest_hash`` — the frozen fork ``manifest_hash`` of the sealed context
  package lineage the candidate was generated from.
- ``citation_hash`` — canonical SHA-256 over the candidate's sorted citation
  keys (the evidence the revision is grounded in).
- ``asset_hashes`` — illustration/asset content hashes bound to the revision
  (empty in Phase 37-04; later phases append auditable entries).
- ``approval`` — the explicit owner review action journal: the override kind /
  reason, the approval state, the approver id/time and the approval note.
- ``review`` — the frozen gate review snapshot the approval was based on: gate
  verdict, gate reason, the CanonDelta hash and the evidence snapshot.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

# Cross-plan contract: exactly these fields may exist on the DTO. The
# adversarial contract test freezes this set (any addition/removal fails).
PUBLISHED_DERIVATIVE_REVISION_FIELDS = frozenset(
    {
        "owner_id",
        "project_id",
        "fork_id",
        "revision_id",
        "version_id",
        "status",
        "source_snapshot",
        "manifest_hash",
        "citation_hash",
        "asset_hashes",
        "approval",
        "review",
    }
)

# D-37-04: derivative-only publication status — never original / promoted.
DERIVATIVE_REVISION_PUBLICATION_STATUS = "derivative_revision"

# Canonical citation hash prefix (byte-replayable evidence lineage).
_CITATION_HASH_PREFIX = "derivative-revision.v1:citations"


@dataclass(frozen=True)
class PublishedDerivativeRevision:
    """Immutable derivative-only publication contract for Phase 39."""

    owner_id: int
    project_id: int
    fork_id: int
    revision_id: int
    version_id: int
    status: str
    source_snapshot: str
    manifest_hash: str
    citation_hash: str
    asset_hashes: list[str]
    approval: dict[str, Any]
    review: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


def canonical_citation_hash(citation_keys: list[str]) -> str:
    """Deterministic SHA-256 over the sorted citation keys (replayable)."""
    encoded = json.dumps(
        sorted(citation_keys or []),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(
        f"{_CITATION_HASH_PREFIX}\n".encode("utf-8") + encoded
    ).hexdigest()


def build_published_derivative_revision(
    *,
    owner_id: int,
    project_id: int,
    fork_id: int,
    revision_id: int,
    version_id: int,
    source_snapshot: str,
    manifest_hash: str,
    citation_keys: list[str],
    approval_state: str,
    approver_id: int,
    approved_at: datetime,
    approval_reason: str,
    override_kind: str,
    override_reason: str,
    gate_verdict: str,
    gate_reason: str | None,
    canon_delta_hash: str,
    evidence_snapshot: dict[str, Any],
    asset_hashes: list[str] | None = None,
) -> PublishedDerivativeRevision:
    """Deterministic DTO builder from an approved override materialization."""
    return PublishedDerivativeRevision(
        owner_id=owner_id,
        project_id=project_id,
        fork_id=fork_id,
        revision_id=revision_id,
        version_id=version_id,
        status=DERIVATIVE_REVISION_PUBLICATION_STATUS,
        source_snapshot=source_snapshot,
        manifest_hash=manifest_hash,
        citation_hash=canonical_citation_hash(citation_keys),
        asset_hashes=list(asset_hashes or []),
        approval={
            "approval_state": approval_state,
            "approver_id": approver_id,
            "approved_at": approved_at.isoformat() if approved_at is not None else None,
            "approval_reason": approval_reason,
            "kind": override_kind,
            "reason": override_reason,
        },
        review={
            "gate_verdict": gate_verdict,
            "gate_reason": gate_reason,
            "canon_delta_hash": canon_delta_hash,
            "evidence_snapshot": dict(evidence_snapshot or {}),
        },
    )


__all__ = [
    "DERIVATIVE_REVISION_PUBLICATION_STATUS",
    "PUBLISHED_DERIVATIVE_REVISION_FIELDS",
    "PublishedDerivativeRevision",
    "build_published_derivative_revision",
    "canonical_citation_hash",
]
