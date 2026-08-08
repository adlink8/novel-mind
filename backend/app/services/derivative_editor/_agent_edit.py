"""Agent-edit proposal primitives for the derivative revision service (leaf).

Extracted from ``revisions.py`` (refactor split): the deterministic approval
payload schema/hash primitives (D-15 / D-36-02) — the proposal result carrier,
the gate error, the content hash and the frozen payload hash — used by
``create_agent_edit_proposal`` in the facade. Leaf by construction: imports only
stdlib + ``chapters.markdown_checksum``, never ``revisions.py``. The revision
facade re-exports these names so the ``app.services.derivative_editor.revisions``
import surface is unchanged.

Note: ``DERIVATIVE_AGENT_EDIT_APPROVAL_ACTION`` (and its ``apply_derivative_edit``
literal) intentionally stays in the facade with the gate/apply write paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.services.derivative_editor.chapters import markdown_checksum

DERIVATIVE_AGENT_EDIT_APPROVAL_PREFIX = "derivative-edit.v1:approval"
DERIVATIVE_AGENT_EDIT_APPROVAL_SCHEMA_VERSION = "derivative-edit-proposal.v1"


class DerivativeEditApplyError(ValueError):
    """Agent-proposal gate violation (fail closed, no authoritative write)."""

    def __init__(self, code: str, detail: str, status_code: int = 409):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class AgentEditProposalResult:
    """Proposal creation result: the pending approval (+ replay flag)."""

    owner_id: int
    novel_id: int
    project_id: int
    chapter_id: int
    approval_request_id: int
    approval_action: str
    approval_status: str
    approval_payload_hash: str
    content_hash: str
    proposal_key: str
    base_revision: int
    replayed: bool = False


def derivative_edit_content_hash(content: str) -> str:
    """Deterministic content hash of a candidate derivative edit (D-36-02).

    The hash is the SHA-256 of the canonical Markdown — the same replay lineage
    the revision row's ``content_checksum`` uses — so the apply endpoint can
    verify the proposal content is byte-replayable before any write.
    """
    return markdown_checksum(content)


def build_derivative_edit_approval_payload(
    *,
    owner_id: int,
    novel_id: int,
    branch: str | None,
    fork: str | None,
    proposal_key: str,
    project_id: int,
    chapter_id: int,
    base_revision: int,
    content_hash: str,
    source_snapshot_hash: str,
) -> dict[str, Any]:
    """Frozen approval payload bound to one derivative edit proposal (D-15).

    The ApprovalRequest ``payload_hash`` and the apply-time replay both compute
    from this canonical snapshot, so a forged or drifted decision can never
    apply a derivative edit. ``base_revision`` seals the exact CAS token and
    ``content_hash`` seals the exact proposed Markdown.
    """
    return {
        "artifact_kind": "derivative_edit_proposal",
        "schema_version": DERIVATIVE_AGENT_EDIT_APPROVAL_SCHEMA_VERSION,
        "owner_id": owner_id,
        "novel_id": novel_id,
        "branch": branch,
        "fork": fork,
        "proposal_key": proposal_key,
        "project_id": project_id,
        "chapter_id": chapter_id,
        "base_revision": base_revision,
        "content_hash": content_hash,
        "source_snapshot_hash": source_snapshot_hash,
    }


def canonical_derivative_edit_approval_hash(payload: dict[str, Any]) -> str:
    """Byte-replayable canonical hash of a frozen derivative-edit approval payload."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(
        f"{DERIVATIVE_AGENT_EDIT_APPROVAL_PREFIX}\n{encoded}".encode("utf-8")
    ).hexdigest()


__all__ = [
    "DERIVATIVE_AGENT_EDIT_APPROVAL_PREFIX",
    "DERIVATIVE_AGENT_EDIT_APPROVAL_SCHEMA_VERSION",
    "AgentEditProposalResult",
    "DerivativeEditApplyError",
    "build_derivative_edit_approval_payload",
    "canonical_derivative_edit_approval_hash",
    "derivative_edit_content_hash",
]
