"""Deterministic, auditable, immutable context package compiler (Phase 37-01).

REQ-FORK-03 / REQ-CRE-05 / D-37-01: before any derivative generation the server
compiles a frozen context package for the specified Canon Fork and cutoff:

    fork manifest -> branch-aware retrieval -> immutable context package

The package freezes, in one canonical JSON payload sealed with a SHA-256
``package_hash``:

- the server-derived cutoff state and the fork/version lineage;
- leaf evidence refs (branch-aware retrieval inside the frozen scope);
- world state / timeline causality / world rules (passed, cutoff-visible rows);
- unresolved clues (published clues whose lifecycle state is still open);
- user intent (continuation | rewrite).

Design conventions (following ``reader_chat/context.py`` and the Phase 35/36
append-only lineage contracts):

- **Server-derived scope:** owner/novel/fork/cutoff always come from the owned
  fork; a requested cutoff can only shrink, never expand (cutoff_exceeds_scope).
- **Honest dimensions:** a missing dimension is ``unavailable`` (or ``blocked``
  with an auditable reason) — never a fake successful empty array, and never
  filled from AI summaries or chat content (REQ-CRE-01 pitfall #4).
- **Deterministic ordering and hashing:** lists are sorted before sealing and
  the hash is computed over canonical JSON (byte-replayable).
- **Budget gate before any provider call:** an over-budget package is blocked
  at compile time; no provider call can ever start on it.
- **No write-back:** compilation only ever persists to the append-only
  ``derivative_context_packages`` table (Fanfiction Canon); no Original Canon
  or User Interpretation write path exists in this module.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canon_fork import CANON_FORK_SPACE, CanonFork
from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisVersion,
    ClueEvidenceRef,
    ClueLifecycleEvent,
    MachineClue,
)
from app.models.derivative_context import (
    DERIVATIVE_CONTEXT_SPACE,
    ContextPackageRecord,
)
from app.models.derivative_project import DERIVATIVE_PROJECT_USABLE_FORK_STATUSES
from app.models.user import User
from app.models.world_model_entity import WorldModelEntity, WorldModelRule
from app.models.world_model_event import WorldModelCausalEdge, WorldModelEvent
from app.services.canon_fork.contracts import CanonSpace, content_sha256
from app.services.canon_fork.retrieval import (
    CanonRetrievalService,
    RetrievalStatus,
    resolve_canon_scope,
)
from app.services.canon_fork.snapshot import CanonForkScopeError

CONTEXT_PACKAGE_SCHEMA_VERSION = "derivative-context.v1"
CONTEXT_PACKAGE_HASH_PREFIX = "derivative-context.v1:package"

# Generation intents (closed vocabulary; unknown intents fail closed).
DERIVATIVE_CONTEXT_INTENTS = ("continuation", "rewrite")
# Clue lifecycle states that still mean "unresolved at the package cutoff".
UNRESOLVED_CLUE_STATES = frozenset({"candidate", "active", "reinforced"})
# Only passed world-model rows are facts; pending/rejected never enter a package.
WORLD_MODEL_GATE_PASSED = "passed"

# Default budget policy (provider calls are blocked before they start).
DEFAULT_BUDGET_MAX_INPUT_TOKENS = 6000
DEFAULT_BUDGET_MAX_EVIDENCE_ITEMS = 64
DEFAULT_BUDGET_MAX_DIMENSION_ITEMS = 200
DEFAULT_CHARS_PER_TOKEN = 4

REQUIRED_LINEAGE_FIELDS = (
    "source_version_key",
    "source_snapshot_hash",
    "through_chapter",
    "full_book_authorized",
    "cutoff_snapshot_hash",
    "scope_hash",
    "manifest_hash",
)
REQUIRED_DIMENSIONS = (
    "world_state",
    "timeline",
    "unresolved_clues",
    "world_rules",
    "evidence",
    "user_intent",
)


class ContextPackageIntent(StrEnum):
    CONTINUATION = "continuation"
    REWRITE = "rewrite"


class DimensionStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"


class ContextPackageError(ValueError):
    """Fail-closed context package gate violation with an HTTP status code."""

    def __init__(self, code: str, detail: str, status_code: int = 400):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ContextBudgetPolicy:
    max_input_tokens: int = DEFAULT_BUDGET_MAX_INPUT_TOKENS
    max_evidence_items: int = DEFAULT_BUDGET_MAX_EVIDENCE_ITEMS
    max_dimension_items: int = DEFAULT_BUDGET_MAX_DIMENSION_ITEMS
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Pure deterministic helpers (DB-free, unit-testable)
# ---------------------------------------------------------------------------


def canonical_json_bytes(payload: Any) -> bytes:
    """Byte-replayable canonical JSON (sorted keys, compact separators)."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def package_hash(payload: dict[str, Any]) -> str:
    """Canonical SHA-256 of the sealed payload, excluding the hash field itself.

    The ``package_hash`` field is excluded so the stored record can be
    re-hashed from its own payload without recursion (T-37-01-02).
    """
    body = {k: v for k, v in payload.items() if k != "package_hash"}
    encoded = canonical_json_bytes(body)
    return sha256(
        f"{CONTEXT_PACKAGE_HASH_PREFIX}\n".encode("utf-8") + encoded
    ).hexdigest()


def verify_package_hash(payload: dict[str, Any], expected_hash: str) -> None:
    """Replay the package hash; a mismatch fails closed (T-37-01-02)."""
    if package_hash(payload) != expected_hash:
        raise ContextPackageError(
            "package_hash_mismatch",
            "package hash does not replay from the canonical payload",
        )


def estimate_input_characters(payload: dict[str, Any]) -> int:
    return len(canonical_json_bytes(payload))


def estimate_input_tokens(
    payload: dict[str, Any], chars_per_token: int = DEFAULT_CHARS_PER_TOKEN
) -> int:
    return max(1, math.ceil(estimate_input_characters(payload) / max(chars_per_token, 1)))


def budget_verdict(
    payload: dict[str, Any], policy: ContextBudgetPolicy | None = None
) -> dict[str, Any]:
    """Deterministic budget gate evaluated before any provider call."""
    policy = policy or ContextBudgetPolicy()
    estimated_tokens = estimate_input_tokens(payload, policy.chars_per_token)
    blocked = estimated_tokens > policy.max_input_tokens
    return {
        "estimated_input_characters": estimate_input_characters(payload),
        "estimated_input_tokens": estimated_tokens,
        "max_input_tokens": policy.max_input_tokens,
        "blocked": blocked,
        "block_reason": "budget_exhausted" if blocked else None,
    }


def compute_dimension_status(items: list[Any] | None) -> DimensionStatus:
    """An empty dimension is ``unavailable`` — never a fake successful array."""
    if not items:
        return DimensionStatus.UNAVAILABLE
    return DimensionStatus.AVAILABLE


def dimension_view(
    *,
    status: DimensionStatus,
    items: list[Any] | None = None,
    version_id: int | None = None,
    block_reason: str | None = None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    view: dict[str, Any] = {"status": status.value}
    if version_id is not None:
        view["version_id"] = version_id
    if trace is not None:
        view["trace"] = trace
    if block_reason is not None:
        view["block_reason"] = block_reason
    view["items"] = list(items or [])
    return view


def assert_cutoff_within_fork(requested: int, fork_cutoff: int) -> None:
    """A future cutoff can never expand the frozen fork scope (D-37-01)."""
    if requested > fork_cutoff:
        raise ContextPackageError(
            "cutoff_exceeds_scope",
            f"requested cutoff {requested} exceeds the frozen fork cutoff "
            f"{fork_cutoff}; a future cutoff cannot expand the package scope",
        )


def validate_lineage(lineage: dict[str, Any]) -> dict[str, Any]:
    """Lineage must carry every frozen fork field before sealing (D-37-01)."""
    missing = [field for field in REQUIRED_LINEAGE_FIELDS if field not in lineage]
    if missing:
        raise ContextPackageError(
            "incomplete_lineage", f"missing lineage fields: {sorted(missing)}"
        )
    through = lineage["through_chapter"]
    if not isinstance(through, int) or through < 1:
        raise ContextPackageError(
            "invalid_cutoff", "through_chapter must be a positive integer"
        )
    return lineage


def assemble_package_payload(
    *,
    owner_id: int,
    novel_id: int,
    fork_id: int,
    fork_key: str,
    intent: str,
    lineage: dict[str, Any],
    dimensions: dict[str, Any],
    budget_estimate: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the canonical package payload with fixed field order.

    Unknown intent, incomplete lineage or a missing dimension fails closed
    before any hash is computed (D-37-01, T-37-01-01).
    """
    if not isinstance(owner_id, int) or owner_id < 1:
        raise ContextPackageError(
            "invalid_scope", "owner_id must be a positive integer"
        )
    if not isinstance(novel_id, int) or novel_id < 1:
        raise ContextPackageError(
            "invalid_scope", "novel_id must be a positive integer"
        )
    if not isinstance(fork_id, int) or fork_id < 1:
        raise ContextPackageError(
            "invalid_scope", "fork_id must be a positive integer"
        )
    try:
        intent_value = ContextPackageIntent(intent).value
    except ValueError:
        raise ContextPackageError(
            "invalid_intent", f"unsupported intent: {intent!r}"
        )
    validate_lineage(lineage)
    missing_dims = [dim for dim in REQUIRED_DIMENSIONS if dim not in dimensions]
    if missing_dims:
        raise ContextPackageError(
            "incomplete_dimensions", f"missing dimensions: {sorted(missing_dims)}"
        )
    return {
        "schema_version": CONTEXT_PACKAGE_SCHEMA_VERSION,
        "owner_id": owner_id,
        "novel_id": novel_id,
        "fork_id": fork_id,
        "fork_key": fork_key,
        "space": DERIVATIVE_CONTEXT_SPACE,
        "intent": intent_value,
        "version": {field: lineage[field] for field in REQUIRED_LINEAGE_FIELDS},
        "dimensions": dimensions,
        "budget_estimate": budget_estimate,
    }


# ---------------------------------------------------------------------------
# Owner/novel-scoped dimension readers (deterministic, read-only)
# ---------------------------------------------------------------------------


async def _latest_dimension_version(
    session: AsyncSession,
    model: Any,
    *,
    owner_id: int,
    novel_id: int,
) -> int | None:
    return await session.scalar(
        select(func.max(model.version_id)).where(
            model.owner_id == owner_id,
            model.novel_id == novel_id,
        )
    )


async def _load_world_state(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    cutoff: int,
) -> dict[str, Any]:
    """Passed, cutoff-visible world entity rows for the latest projection."""
    version = await _latest_dimension_version(
        session, WorldModelEntity, owner_id=owner_id, novel_id=novel_id
    )
    if version is None:
        return dimension_view(status=DimensionStatus.UNAVAILABLE)
    rows = list(
        (
            await session.scalars(
                select(WorldModelEntity)
                .where(
                    WorldModelEntity.owner_id == owner_id,
                    WorldModelEntity.novel_id == novel_id,
                    WorldModelEntity.version_id == version,
                    WorldModelEntity.gate_status == WORLD_MODEL_GATE_PASSED,
                    WorldModelEntity.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelEntity.entity_key.asc(), WorldModelEntity.id.asc())
            )
        ).all()
    )
    items = [
        {
            "entity_key": row.entity_key,
            "entity_type": row.entity_type,
            "authority": row.authority,
            "confidence": float(row.confidence),
            "disclosure_cutoff": row.disclosure_cutoff,
            "aliases": list(row.aliases or []),
            "source_refs": list(row.source_refs or []),
            "canonical_payload": dict(row.canonical_payload or {}),
            "canonical_payload_hash": row.canonical_payload_hash,
        }
        for row in rows
    ]
    return dimension_view(
        status=compute_dimension_status(items),
        items=items,
        version_id=version,
    )


async def _load_world_rules(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    cutoff: int,
) -> dict[str, Any]:
    """Passed, cutoff-visible world rule rows for the latest projection."""
    version = await _latest_dimension_version(
        session, WorldModelRule, owner_id=owner_id, novel_id=novel_id
    )
    if version is None:
        return dimension_view(status=DimensionStatus.UNAVAILABLE)
    rows = list(
        (
            await session.scalars(
                select(WorldModelRule)
                .where(
                    WorldModelRule.owner_id == owner_id,
                    WorldModelRule.novel_id == novel_id,
                    WorldModelRule.version_id == version,
                    WorldModelRule.gate_status == WORLD_MODEL_GATE_PASSED,
                    WorldModelRule.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelRule.rule_key.asc(), WorldModelRule.id.asc())
            )
        ).all()
    )
    items = [
        {
            "rule_key": row.rule_key,
            "authority": row.authority,
            "confidence": float(row.confidence),
            "disclosure_cutoff": row.disclosure_cutoff,
            "source_refs": list(row.source_refs or []),
            "canonical_payload": dict(row.canonical_payload or {}),
            "canonical_payload_hash": row.canonical_payload_hash,
        }
        for row in rows
    ]
    return dimension_view(
        status=compute_dimension_status(items),
        items=items,
        version_id=version,
    )


async def _load_timeline(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    cutoff: int,
) -> dict[str, Any]:
    """Passed, cutoff-visible events and causal edges for the latest projection."""
    version = await _latest_dimension_version(
        session, WorldModelEvent, owner_id=owner_id, novel_id=novel_id
    )
    if version is None:
        return dimension_view(status=DimensionStatus.UNAVAILABLE)
    event_rows = list(
        (
            await session.scalars(
                select(WorldModelEvent)
                .where(
                    WorldModelEvent.owner_id == owner_id,
                    WorldModelEvent.novel_id == novel_id,
                    WorldModelEvent.version_id == version,
                    WorldModelEvent.gate_status == WORLD_MODEL_GATE_PASSED,
                    WorldModelEvent.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelEvent.event_key.asc(), WorldModelEvent.id.asc())
            )
        ).all()
    )
    edge_rows = list(
        (
            await session.scalars(
                select(WorldModelCausalEdge)
                .where(
                    WorldModelCausalEdge.owner_id == owner_id,
                    WorldModelCausalEdge.novel_id == novel_id,
                    WorldModelCausalEdge.version_id == version,
                    WorldModelCausalEdge.gate_status == WORLD_MODEL_GATE_PASSED,
                    WorldModelCausalEdge.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelCausalEdge.edge_key.asc(), WorldModelCausalEdge.id.asc())
            )
        ).all()
    )
    items = [
        {
            "event_key": row.event_key,
            "authority": row.authority,
            "confidence": float(row.confidence),
            "effective_start": row.effective_start,
            "effective_end": row.effective_end,
            "disclosure_cutoff": row.disclosure_cutoff,
            "source_refs": list(row.source_refs or []),
            "canonical_payload": dict(row.canonical_payload or {}),
            "canonical_payload_hash": row.canonical_payload_hash,
        }
        for row in event_rows
    ]
    items.extend(
        {
            "edge_key": row.edge_key,
            "source_event_key": row.source_event_key,
            "target_event_key": row.target_event_key,
            "edge_type": row.edge_type,
            "authority": row.authority,
            "confidence": float(row.confidence),
            "disclosure_cutoff": row.disclosure_cutoff,
            "source_refs": list(row.source_refs or []),
            "canonical_payload": dict(row.canonical_payload or {}),
            "canonical_payload_hash": row.canonical_payload_hash,
        }
        for row in edge_rows
    )
    return dimension_view(
        status=compute_dimension_status(items),
        items=items,
        version_id=version,
    )


async def _resolve_clue_version(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
) -> int | None:
    """Active clue version, falling back to the latest validated version."""
    pointer = await session.scalar(
        select(ClueActivePointer).where(
            ClueActivePointer.owner_id == owner_id,
            ClueActivePointer.novel_id == novel_id,
        )
    )
    if pointer is not None:
        return pointer.version_id
    return await session.scalar(
        select(ClueAnalysisVersion.id)
        .where(
            ClueAnalysisVersion.owner_id == owner_id,
            ClueAnalysisVersion.novel_id == novel_id,
            ClueAnalysisVersion.status == "validated",
        )
        .order_by(ClueAnalysisVersion.id.desc())
        .limit(1)
    )


async def _load_unresolved_clues(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    cutoff: int,
) -> dict[str, Any]:
    """Published clues whose lifecycle state is still open at the cutoff.

    The current state is derived by replaying lifecycle events (latest event per
    logical clue id). A clue already paid off or dismissed is not unresolved;
    a missing clue version is ``unavailable`` — never fabricated.
    """
    version = await _resolve_clue_version(
        session, owner_id=owner_id, novel_id=novel_id
    )
    if version is None:
        return dimension_view(status=DimensionStatus.UNAVAILABLE)

    event_rows = list(
        (
            await session.scalars(
                select(ClueLifecycleEvent)
                .where(ClueLifecycleEvent.version_id == version)
                .order_by(
                    ClueLifecycleEvent.logical_clue_id, ClueLifecycleEvent.id
                )
            )
        ).all()
    )
    latest_state: dict[str, str] = {}
    for event in event_rows:
        latest_state[event.logical_clue_id] = event.to_status

    clue_rows = list(
        (
            await session.scalars(
                select(MachineClue)
                .where(
                    MachineClue.owner_id == owner_id,
                    MachineClue.novel_id == novel_id,
                    MachineClue.version_id == version,
                    MachineClue.publication_status == "published",
                )
                .order_by(MachineClue.logical_clue_id.asc())
            )
        ).all()
    )
    evidence_rows = list(
        (
            await session.scalars(
                select(ClueEvidenceRef)
                .where(
                    ClueEvidenceRef.owner_id == owner_id,
                    ClueEvidenceRef.novel_id == novel_id,
                    ClueEvidenceRef.version_id == version,
                    ClueEvidenceRef.narrative_chapter_number <= cutoff,
                )
                .order_by(
                    ClueEvidenceRef.logical_clue_id.asc(),
                    ClueEvidenceRef.role.asc(),
                    ClueEvidenceRef.evidence_identity.asc(),
                )
            )
        ).all()
    )
    evidence_by_clue: dict[str, list[dict[str, Any]]] = {}
    for ref in evidence_rows:
        evidence_by_clue.setdefault(ref.logical_clue_id, []).append(
            {
                "role": ref.role,
                "evidence_id": ref.evidence_id,
                "evidence_identity": ref.evidence_identity,
                "narrative_chapter_number": ref.narrative_chapter_number,
                "source_start": ref.source_start,
                "source_end": ref.source_end,
                "content_hash": ref.content_hash,
            }
        )

    items: list[dict[str, Any]] = []
    for clue in clue_rows:
        state = latest_state.get(clue.logical_clue_id, "candidate")
        if state not in UNRESOLVED_CLUE_STATES:
            continue
        if clue.first_cue_chapter is not None and clue.first_cue_chapter > cutoff:
            # The clue is not yet visible at the package cutoff.
            continue
        items.append(
            {
                "logical_clue_id": clue.logical_clue_id,
                "title": clue.title,
                "summary": clue.summary,
                "status": state,
                "confidence": float(clue.confidence),
                "first_cue_chapter": clue.first_cue_chapter,
                "package_hash": clue.package_hash,
                "evidence_refs": evidence_by_clue.get(clue.logical_clue_id, []),
            }
        )
    return dimension_view(
        status=compute_dimension_status(items),
        items=items,
        version_id=version,
    )


def _retrieval_trace_view(trace: Any) -> dict[str, Any]:
    return {
        "scope_hash": trace.scope_hash,
        "space": trace.space.value,
        "through_chapter": trace.through_chapter,
        "loaded_scoped_count": trace.loaded_scoped_count,
        "beyond_cutoff_count": trace.beyond_cutoff_count,
        "stale_snapshot_count": trace.stale_snapshot_count,
        "ranked_count": trace.ranked_count,
        "status": trace.status.value,
        "block_reason": (
            trace.block_reason.value if trace.block_reason is not None else None
        ),
    }


def _evidence_dimension(result: Any) -> dict[str, Any]:
    """Evidence dimension from the branch-aware retrieval result.

    Blocked/absent retrieval is reported honestly — never a fake successful
    empty array (REQ-CRE-01 pitfall #4).
    """
    items = [candidate.evidence_ref for candidate in result.candidates]
    trace = _retrieval_trace_view(result.trace)
    if items:
        status = DimensionStatus.AVAILABLE
    elif result.trace.status is RetrievalStatus.BLOCKED:
        status = DimensionStatus.BLOCKED
    else:
        status = DimensionStatus.UNAVAILABLE
    return dimension_view(
        status=status,
        items=items,
        trace=trace,
        block_reason=(
            trace["block_reason"] if status is DimensionStatus.BLOCKED else None
        ),
    )


# ---------------------------------------------------------------------------
# Compiler service (fork manifest -> branch-aware retrieval -> sealed package)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextPackageCompileResult:
    package: ContextPackageRecord
    payload: dict[str, Any]
    package_hash: str
    replayed: bool


class ContextPackageCompiler:
    """Compiles and seals one immutable context package for an owned fork."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        budget_policy: ContextBudgetPolicy | None = None,
    ) -> None:
        self._session = session
        self._budget_policy = budget_policy or ContextBudgetPolicy()

    async def compile(
        self,
        *,
        owner_id: int,
        novel_id: int,
        user: User,
        fork_id: int,
        intent: str,
        through_chapter: int | None = None,
    ) -> ContextPackageCompileResult:
        """Compile a sealed package; a budget overrun blocks before any call."""
        fork = await self._load_scoped_fork(
            owner_id=owner_id, novel_id=novel_id, fork_id=fork_id
        )
        if through_chapter is not None:
            assert_cutoff_within_fork(through_chapter, fork.through_chapter)

        # branch-aware retrieval scope (server-derived; client can only shrink).
        try:
            scope = await resolve_canon_scope(
                self._session,
                owner_id=owner_id,
                novel_id=novel_id,
                user=user,
                space=CanonSpace.FANFICTION_CANON,
                fork_id=fork.id,
                through_chapter=through_chapter,
            )
        except CanonForkScopeError as exc:
            raise ContextPackageError(
                exc.code, exc.detail, status_code=getattr(exc, "status_code", 400)
            ) from exc

        cutoff = scope.through_chapter
        retrieval = await CanonRetrievalService(self._session).retrieve(scope)

        dimensions = {
            "world_state": await _load_world_state(
                self._session, owner_id=owner_id, novel_id=novel_id, cutoff=cutoff
            ),
            "timeline": await _load_timeline(
                self._session, owner_id=owner_id, novel_id=novel_id, cutoff=cutoff
            ),
            "unresolved_clues": await _load_unresolved_clues(
                self._session, owner_id=owner_id, novel_id=novel_id, cutoff=cutoff
            ),
            "world_rules": await _load_world_rules(
                self._session, owner_id=owner_id, novel_id=novel_id, cutoff=cutoff
            ),
            "evidence": _evidence_dimension(retrieval),
            "user_intent": {
                "status": DimensionStatus.AVAILABLE.value,
                "kind": ContextPackageIntent(intent).value,
                "hash": content_sha256(ContextPackageIntent(intent).value),
            },
        }
        lineage = {
            "source_version_key": fork.source_version_key,
            "source_snapshot_hash": fork.source_snapshot_hash,
            "through_chapter": cutoff,
            "full_book_authorized": bool(fork.full_book_authorized),
            # The resolved scope's cutoff/scope hashes reflect the *actual*
            # frozen cutoff (a requested shrink narrows the package scope).
            "cutoff_snapshot_hash": scope.cutoff.snapshot_hash,
            "scope_hash": scope.scope_hash(),
            "manifest_hash": fork.manifest_hash,
        }

        core = assemble_package_payload(
            owner_id=owner_id,
            novel_id=novel_id,
            fork_id=fork.id,
            fork_key=fork.fork_key,
            intent=intent,
            lineage=lineage,
            dimensions=dimensions,
            budget_estimate={},
        )
        budget_estimate = budget_verdict(core, self._budget_policy)
        if budget_estimate["blocked"]:
            raise ContextPackageError(
                "budget_exhausted",
                f"estimated {budget_estimate['estimated_input_tokens']} input "
                f"tokens exceeds the policy maximum "
                f"{budget_estimate['max_input_tokens']}; the package is blocked "
                "before any provider call",
                status_code=422,
            )
        payload = dict(core)
        payload["budget_estimate"] = budget_estimate
        sealed_hash = package_hash(payload)

        package_key = f"ctx:{fork.fork_key}:{intent}:{cutoff}"
        row, replayed = await self._persist_package(
            owner_id=owner_id,
            novel_id=novel_id,
            fork=fork,
            intent=intent,
            cutoff=cutoff,
            lineage=lineage,
            payload=payload,
            sealed_hash=sealed_hash,
            budget_estimate=budget_estimate,
            package_key=package_key,
        )
        return ContextPackageCompileResult(
            package=row, payload=payload, package_hash=sealed_hash, replayed=replayed
        )

    async def _load_scoped_fork(
        self, *, owner_id: int, novel_id: int, fork_id: int
    ) -> CanonFork:
        fork = await self._session.scalar(
            select(CanonFork).where(
                CanonFork.id == fork_id,
                CanonFork.owner_id == owner_id,
                CanonFork.novel_id == novel_id,
            )
        )
        if fork is None:
            raise ContextPackageError(
                "fork_not_found",
                "canon fork not found in the owner/novel scope",
                status_code=404,
            )
        if fork.space != CANON_FORK_SPACE:
            raise ContextPackageError(
                "fork_space_denied",
                "only fanfiction_canon forks can anchor a context package",
                status_code=403,
            )
        if fork.status not in DERIVATIVE_PROJECT_USABLE_FORK_STATUSES:
            raise ContextPackageError(
                "fork_not_usable",
                f"fork {fork.id} is {fork.status!r}; rejected/archived forks "
                "cannot anchor a context package",
                status_code=409,
            )
        return fork

    async def _persist_package(
        self,
        *,
        owner_id: int,
        novel_id: int,
        fork: CanonFork,
        intent: str,
        cutoff: int,
        lineage: dict[str, Any],
        payload: dict[str, Any],
        sealed_hash: str,
        budget_estimate: dict[str, Any],
        package_key: str,
    ) -> tuple[ContextPackageRecord, bool]:
        """Append-only persist; an identical replay returns the existing row."""
        existing = await self._session.scalar(
            select(ContextPackageRecord).where(
                ContextPackageRecord.owner_id == owner_id,
                ContextPackageRecord.novel_id == novel_id,
                ContextPackageRecord.package_key == package_key,
            )
        )
        if existing is not None:
            if existing.package_hash == sealed_hash:
                return existing, True
            raise ContextPackageError(
                "package_key_conflict",
                f"package_key {package_key!r} is already sealed with a different "
                "frozen payload; a package is immutable",
                status_code=409,
            )
        row = ContextPackageRecord(
            owner_id=owner_id,
            novel_id=novel_id,
            fork_id=fork.id,
            package_key=package_key,
            space=DERIVATIVE_CONTEXT_SPACE,
            intent=intent,
            fork_key=fork.fork_key,
            source_version_key=lineage["source_version_key"],
            source_snapshot_hash=lineage["source_snapshot_hash"],
            through_chapter=cutoff,
            full_book_authorized=lineage["full_book_authorized"],
            cutoff_snapshot_hash=lineage["cutoff_snapshot_hash"],
            scope_hash=lineage["scope_hash"],
            manifest_hash=lineage["manifest_hash"],
            canonical_payload=payload,
            budget_estimate=budget_estimate,
            package_hash=sealed_hash,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row, False

    async def list_packages(
        self, *, owner_id: int, novel_id: int
    ) -> list[ContextPackageRecord]:
        return list(
            (
                await self._session.scalars(
                    select(ContextPackageRecord)
                    .where(
                        ContextPackageRecord.owner_id == owner_id,
                        ContextPackageRecord.novel_id == novel_id,
                    )
                    .order_by(ContextPackageRecord.id.desc())
                )
            ).all()
        )

    async def get_package(
        self, *, owner_id: int, novel_id: int, package_id: int
    ) -> ContextPackageRecord:
        row = await self._session.scalar(
            select(ContextPackageRecord).where(
                ContextPackageRecord.id == package_id,
                ContextPackageRecord.owner_id == owner_id,
                ContextPackageRecord.novel_id == novel_id,
            )
        )
        if row is None:
            raise ContextPackageError(
                "package_not_found",
                "context package not found in the owner/novel scope",
                status_code=404,
            )
        # Replay the hash: a drifted/forged row fails closed (T-37-01-02).
        verify_package_hash(dict(row.canonical_payload or {}), row.package_hash)
        return row


__all__ = [
    "CONTEXT_PACKAGE_HASH_PREFIX",
    "CONTEXT_PACKAGE_SCHEMA_VERSION",
    "ContextBudgetPolicy",
    "ContextPackageCompileResult",
    "ContextPackageCompiler",
    "ContextPackageError",
    "ContextPackageIntent",
    "DimensionStatus",
    "REQUIRED_DIMENSIONS",
    "REQUIRED_LINEAGE_FIELDS",
    "assemble_package_payload",
    "assert_cutoff_within_fork",
    "budget_verdict",
    "canonical_json_bytes",
    "compute_dimension_status",
    "dimension_view",
    "estimate_input_characters",
    "estimate_input_tokens",
    "package_hash",
    "validate_lineage",
    "verify_package_hash",
]
