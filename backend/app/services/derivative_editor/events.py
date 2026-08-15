"""Event name / actor-label owner for the Phase 36 derivative editor CAS paths.

D-36-02 + the 36-05 Agent Consumer Contract: ``user_autosave`` and
``agent_proposal`` must stay **disjoint** on every axis — separate FastAPI
endpoints, separate event names, separate actor labels and separate CAS
paths. This module is the single owner of those canonical names and of the
structured event record shape:

- ``derivative.user_autosave.accepted`` — a user draft autosave committed a new
  immutable ``autosave`` revision row (or resolved idempotently as ``noop``).
- ``derivative.user_autosave.conflict`` — a stale/concurrent user autosave hit
  the base_revision CAS and was rejected with the latest revision (409).
- ``derivative.agent_proposal.applied`` — the deterministic Revision Service
  applied an approved DerivativeEditProposal as an append-only
  ``agent_proposal`` revision.
- ``derivative.agent_proposal.rejected`` — a derivative edit apply attempt
  failed closed (forged/expired/rejected approval, stale base, wrong
  branch/fork, schema drift, cancellation) with no authoritative write.

Actor labels:
- ``derivative.user_autosave`` — the browser/editor autosave path.
- ``derivative.agent_proposal`` — the Agent/Web-approval path.

Neither endpoint consumes the other's approval or event: a user autosave never
satisfies an ``apply_derivative_edit`` ApprovalRequest and an agent proposal is
never emitted as a user autosave event. Events are emitted through the
``logging`` sink (structured records) because there is no outbox table in this
surface; the constants are the authoritative contract and tests assert the
two paths are disjoint on the wire.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

# ────────────────────────── event names (single owner) ──────────────────────────

DERIVATIVE_USER_AUTOSAVE_ACCEPTED = "derivative.user_autosave.accepted"
DERIVATIVE_USER_AUTOSAVE_CONFLICT = "derivative.user_autosave.conflict"
DERIVATIVE_AGENT_PROPOSAL_APPLIED = "derivative.agent_proposal.applied"
DERIVATIVE_AGENT_PROPOSAL_REJECTED = "derivative.agent_proposal.rejected"

# ────────────────────────── actor labels (single owner) ──────────────────────────

DERIVATIVE_ACTOR_USER_AUTOSAVE = "derivative.user_autosave"
DERIVATIVE_ACTOR_AGENT_PROPOSAL = "derivative.agent_proposal"

# The user-autosave path owns exactly these two events; the agent-proposal path
# owns exactly these two. Disjointness is asserted by tests.
DERIVATIVE_USER_AUTOSAVE_EVENTS: frozenset[str] = frozenset(
    {DERIVATIVE_USER_AUTOSAVE_ACCEPTED, DERIVATIVE_USER_AUTOSAVE_CONFLICT}
)
DERIVATIVE_AGENT_PROPOSAL_EVENTS: frozenset[str] = frozenset(
    {DERIVATIVE_AGENT_PROPOSAL_APPLIED, DERIVATIVE_AGENT_PROPOSAL_REJECTED}
)

_logger = logging.getLogger("derivative_events")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_derivative_event(
    event_name: str,
    *,
    actor: str,
    scope: dict[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    """Build one structured derivative-editor event record.

    The record carries the canonical event name, the actor label and the
    owner/novel/branch/project/chapter scope plus the outcome detail. Both
    paths emit through this builder so the event shape is a single contract.
    """
    return {
        "event": event_name,
        "actor": actor,
        "scope": dict(scope),
        "outcome": dict(outcome),
        "occurred_at": _utcnow_iso(),
    }


def build_user_autosave_event(
    *,
    event: str,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    base_revision: int,
    status: str,
) -> dict[str, Any]:
    """user_autosave path: accepted (saved/noop) or conflict."""
    if event not in DERIVATIVE_USER_AUTOSAVE_EVENTS:
        raise ValueError(
            f"{event!r} is not a user-autosave event; user_autosave owns only "
            f"{sorted(DERIVATIVE_USER_AUTOSAVE_EVENTS)}"
        )
    return build_derivative_event(
        event,
        actor=DERIVATIVE_ACTOR_USER_AUTOSAVE,
        scope={
            "owner_id": owner_id,
            "novel_id": novel_id,
            "project_id": project_id,
            "chapter_id": chapter_id,
            "base_revision": base_revision,
        },
        outcome={"status": status},
    )


def build_agent_proposal_event(
    *,
    event: str,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    proposal_key: str,
    base_revision: int,
    status: str,
) -> dict[str, Any]:
    """agent_proposal path: applied or rejected (deterministic Revision Service)."""
    if event not in DERIVATIVE_AGENT_PROPOSAL_EVENTS:
        raise ValueError(
            f"{event!r} is not an agent-proposal event; agent_proposal owns only "
            f"{sorted(DERIVATIVE_AGENT_PROPOSAL_EVENTS)}"
        )
    return build_derivative_event(
        event,
        actor=DERIVATIVE_ACTOR_AGENT_PROPOSAL,
        scope={
            "owner_id": owner_id,
            "novel_id": novel_id,
            "project_id": project_id,
            "chapter_id": chapter_id,
            "proposal_key": proposal_key,
            "base_revision": base_revision,
        },
        outcome={"status": status},
    )


def emit_derivative_event(event: dict[str, Any]) -> None:
    """Emit one structured derivative-editor event through the structured logger.

    There is no outbox table in this surface; the structured log record is the
    observable sink and the constants above are the authoritative contract.
    """
    _logger.info("derivative_event %s", event)


__all__ = [
    "DERIVATIVE_ACTOR_AGENT_PROPOSAL",
    "DERIVATIVE_ACTOR_USER_AUTOSAVE",
    "DERIVATIVE_AGENT_PROPOSAL_APPLIED",
    "DERIVATIVE_AGENT_PROPOSAL_EVENTS",
    "DERIVATIVE_AGENT_PROPOSAL_REJECTED",
    "DERIVATIVE_USER_AUTOSAVE_ACCEPTED",
    "DERIVATIVE_USER_AUTOSAVE_CONFLICT",
    "DERIVATIVE_USER_AUTOSAVE_EVENTS",
    "build_agent_proposal_event",
    "build_derivative_event",
    "build_user_autosave_event",
    "emit_derivative_event",
]
