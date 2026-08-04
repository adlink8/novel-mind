"""Derivative asset candidate storage and review lineage (Phase 38-03).

REQ-FORK-04 / REQ-CRE-06 / D-38-03: a generated derivative visual asset is
stored as a **write-only isolated candidate**. This module owns:

- ``DerivativeAssetStorage`` — content-hash addressed bytes under the
  allowlisted derivative storage root (``derivative_assets/...``). Path
  traversal fails closed, MIME/size are allowlisted, and the content checksum
  always replays from the bytes; no raw path is ever exposed to clients.
- ``store_derivative_candidate_asset`` — the owner-scoped deterministic store
  gate: revalidates the frozen canonical derivative Scene Spec (replays its
  content hash), requires the approved derivative visual fork version in scope,
  verifies the claimed checksum, the divergence manifest hash and that the
  identity/source lineage matches the spec exactly (identity drift / mixed
  authority blocked), computes the deterministic cross-chapter consistency
  report over the same-identity sibling candidates and persists an immutable
  candidate row. A duplicate ``asset_key`` with identical content replays; a
  conflicting retry fails closed. The Original Visual Bible rows are never
  touched.
- ``apply_derivative_asset_review`` — append-only, idempotent approve/reject/
  supersede actions that only move the candidate ``review_state`` projection.
  A ``blocked`` candidate (identity drift / undeclared divergence) can never be
  approved.
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_visual import (
    DERIVATIVE_ASSET_NAMESPACE,
    DerivativeVisualCandidateAsset,
    DerivativeVisualCandidateReviewEvent,
    DerivativeVisualVersion,
)
from app.schemas.derivative_visual import DerivativeSceneSpecContract
from app.schemas.derivative_visual_asset import (
    DERIVATIVE_ASSET_SCHEMA_VERSION,
    DerivativeAssetCandidateWrite,
    DerivativeAssetIdentityRow,
    DerivativeAssetReviewEventInput,
    DerivativeAssetSourceRef,
    canonical_derivative_asset_hash,
    chapter_evidence_from_spec,
    derivative_asset_review_state_after,
    divergence_manifest_hash_from_spec,
    review_state_from_consistency_verdict,
)
from app.services.derivative_visual.consistency import score_cross_chapter_consistency

# Storage constants mirror the Phase 33 content-hash storage pattern but with a
# sealed derivative scope prefix. The DB row stays authoritative for MIME/bytes;
# no filesystem path is ever a client-visible identity.
DERIVATIVE_ASSET_SCOPE_PREFIX = "derivative_assets"
ALLOWED_DERIVATIVE_MIME_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
MAX_DERIVATIVE_ASSET_BYTES = 20 * 1024 * 1024  # 20 MiB worst-case reference asset


class DerivativeAssetStorageError(ValueError):
    """Fail-closed derivative asset storage gate violation."""


class DerivativeAssetNotFound(DerivativeAssetStorageError):
    """The candidate bytes do not exist (or are outside the caller's scope)."""


class DerivativeCandidateConflict(ValueError):
    """A conflicting candidate retry that cannot replay (fail closed)."""


class DerivativeCandidateScopeError(ValueError):
    """A candidate/spec/version is outside the explicit owner/novel scope."""


class DerivativeAssetReviewError(ValueError):
    """Illegal / conflicting derivative asset review action."""


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise DerivativeCandidateScopeError(
            "scope identifiers must be explicit positive integers"
        )


def generate_derivative_asset_id() -> str:
    """Generated asset id; the client can never supply a storage identity."""
    return f"dv-{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Storage (allowlisted derivative root, generated ids, content checksum)
# ---------------------------------------------------------------------------


class DerivativeAssetStorage:
    """Content-hash addressed candidate bytes under the derivative root."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root = Path(root_dir).resolve()

    def store(
        self,
        *,
        owner_id: int,
        novel_id: int,
        visual_version_id: int,
        asset_id: str,
        mime_type: str,
        payload: bytes,
    ) -> str:
        """Validate and persist candidate bytes; returns the relative key."""
        self._require_scope(owner_id, novel_id)
        if not payload:
            raise DerivativeAssetStorageError("cannot store an empty asset")
        if len(payload) > MAX_DERIVATIVE_ASSET_BYTES:
            raise DerivativeAssetStorageError(
                f"candidate payload exceeds the {MAX_DERIVATIVE_ASSET_BYTES} byte limit"
            )
        extension = ALLOWED_DERIVATIVE_MIME_TYPES.get(mime_type)
        if extension is None:
            raise DerivativeAssetStorageError(
                f"unsupported candidate mime_type {mime_type!r}; allowed: "
                f"{sorted(ALLOWED_DERIVATIVE_MIME_TYPES)}"
            )
        if not isinstance(asset_id, str) or not asset_id:
            raise DerivativeAssetStorageError("asset_id must be a non-empty string")

        storage_key = (
            f"{DERIVATIVE_ASSET_SCOPE_PREFIX}/{owner_id}/{novel_id}/"
            f"{visual_version_id}/{asset_id}{extension}"
        )
        target = self._resolve(owner_id, novel_id, storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, payload)
        return storage_key

    def read(
        self,
        *,
        owner_id: int,
        novel_id: int,
        visual_version_id: int,
        asset_id: str,
        mime_type: str,
    ) -> bytes:
        """Read candidate bytes; traversal/scope failures fail closed."""
        storage_key = (
            f"{DERIVATIVE_ASSET_SCOPE_PREFIX}/{owner_id}/{novel_id}/"
            f"{visual_version_id}/{asset_id}"
            f"{ALLOWED_DERIVATIVE_MIME_TYPES.get(mime_type, '')}"
        )
        target = self._resolve(owner_id, novel_id, storage_key)
        if not target.is_file():
            raise DerivativeAssetNotFound(
                f"candidate asset {asset_id!r} does not exist in the scope"
            )
        return target.read_bytes()

    def exists(
        self,
        *,
        owner_id: int,
        novel_id: int,
        visual_version_id: int,
        asset_id: str,
        mime_type: str,
    ) -> bool:
        try:
            return self.read(
                owner_id=owner_id,
                novel_id=novel_id,
                visual_version_id=visual_version_id,
                asset_id=asset_id,
                mime_type=mime_type,
            ) is not None
        except DerivativeAssetStorageError:
            return False

    def remove(
        self,
        *,
        owner_id: int,
        novel_id: int,
        visual_version_id: int,
        asset_id: str,
        mime_type: str,
    ) -> None:
        storage_key = (
            f"{DERIVATIVE_ASSET_SCOPE_PREFIX}/{owner_id}/{novel_id}/"
            f"{visual_version_id}/{asset_id}"
            f"{ALLOWED_DERIVATIVE_MIME_TYPES.get(mime_type, '')}"
        )
        target = self._resolve(owner_id, novel_id, storage_key)
        if target.is_file():
            target.unlink()

    # ------------------------------------------------------------- containment

    def _resolve(self, owner_id: int, novel_id: int, storage_key: str) -> Path:
        """Resolve a storage_key and fail closed on scope/path traversal.

        The containment root is the **version scope** (``prefix/owner/novel/
        version_id``): an asset_id such as ``../evil`` or ``../../evil`` that
        climbs out of its own version/owner/novel directory fails closed.
        """
        self._require_scope(owner_id, novel_id)
        if not isinstance(storage_key, str) or not storage_key:
            raise DerivativeAssetStorageError("storage_key must be a non-empty string")
        expected_prefix = f"{DERIVATIVE_ASSET_SCOPE_PREFIX}/{owner_id}/{novel_id}/"
        if not storage_key.startswith(expected_prefix):
            raise DerivativeAssetStorageError(
                "storage_key is outside the owner/novel scope"
            )
        parts = storage_key.split("/")
        if len(parts) < 5:
            raise DerivativeAssetStorageError(
                "storage_key must carry the version scope segment"
            )
        version_segment = parts[3]
        if not version_segment.isdigit() or int(version_segment) <= 0:
            raise DerivativeAssetStorageError(
                "storage_key version segment must be a positive integer"
            )
        candidate = (self.root / storage_key).resolve()
        scope_root = (
            self.root
            / DERIVATIVE_ASSET_SCOPE_PREFIX
            / str(owner_id)
            / str(novel_id)
            / version_segment
        ).resolve()
        try:
            candidate.relative_to(scope_root)
        except ValueError:
            raise DerivativeAssetStorageError(
                "storage_key escapes the owner/novel derivative asset scope"
            ) from None
        return candidate

    @staticmethod
    def _atomic_write(target: Path, payload: bytes) -> None:
        fd, temp_path = tempfile.mkstemp(
            prefix=".dvc-", dir=str(target.parent), suffix=".tmp"
        )
        try:
            with open(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                import os

                os.fsync(handle.fileno())
            temp = Path(temp_path)
            if target.exists():
                target.unlink()
            temp.replace(target)
        except BaseException:
            Path(temp_path).unlink(missing_ok=True)
            raise

    @staticmethod
    def _require_scope(owner_id: int, novel_id: int) -> None:
        if not isinstance(owner_id, int) or not isinstance(novel_id, int):
            raise DerivativeAssetStorageError("owner/novel scope must be integers")
        if owner_id <= 0 or novel_id <= 0:
            raise DerivativeAssetStorageError(
                "owner/novel scope must be explicit positive integers"
            )

    def default_root(self) -> Path:
        return self.root

    @staticmethod
    def default_storage_root() -> Path:
        """Deployment default derivative root; never exposed to clients."""
        from app.config import settings

        base = Path(getattr(settings, "storage_dir", None) or "storage")
        return base / "derivative_asset_candidates"


# ---------------------------------------------------------------------------
# Candidate store gate (D-38-03: deterministic, owner-scoped, fail-closed)
# ---------------------------------------------------------------------------


def _spec_identity_rows(spec: DerivativeSceneSpecContract) -> list[dict[str, Any]]:
    """Server-derived identity lineage from the frozen spec (hash-pinned)."""
    rows: list[dict[str, Any]] = []
    for row in spec.identity:
        ref = dict(row.source_entity_ref or {})
        rows.append(
            {
                "stable_id": row.stable_id,
                "entity_key": row.entity_key,
                "entity_type": row.entity_type.value,
                "source_entity_hash": str(ref.get("source_entity_hash", "")),
            }
        )
    return rows


def _spec_source_refs(spec: DerivativeSceneSpecContract) -> list[dict[str, Any]]:
    """Server-derived Original source refs from the frozen spec."""
    refs: list[dict[str, Any]] = []
    for asset in spec.reference_assets:
        ref = dict(asset.source_asset_ref or {})
        refs.append(
            {
                "asset_key": asset.asset_key,
                "asset_id": asset.asset_id,
                "source_asset_id": str(ref.get("source_asset_id", "")),
                "source_bytes_hash": str(ref.get("source_bytes_hash", "")),
            }
        )
    return refs


def _candidate_payload(
    *,
    owner_id: int,
    novel_id: int,
    version: DerivativeVisualVersion,
    spec: DerivativeSceneSpecContract,
    candidate: DerivativeAssetCandidateWrite,
    asset_id: str,
    storage_key: str,
    content_hash: str,
    identity_lineage: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    consistency_evidence: dict[str, Any],
    report: dict[str, Any],
    review_state: str,
) -> dict[str, Any]:
    """Canonical payload that freezes the candidate's full lineage."""
    return {
        "artifact_kind": "derivative_visual_candidate",
        "schema_version": DERIVATIVE_ASSET_SCHEMA_VERSION,
        "owner_id": owner_id,
        "novel_id": novel_id,
        "project_id": version.project_id,
        "fork_id": version.fork_id,
        "visual_version_id": version.id,
        "visual_version_hash": version.canonical_payload_hash,
        "version_key": version.version_key,
        "asset_key": candidate.asset_key,
        "asset_id": asset_id,
        "storage_key": storage_key,
        "mime_type": candidate.mime_type,
        "content_hash": content_hash,
        "chapter_number": candidate.chapter_number,
        "scene_spec_hash": spec.content_hash,
        "source_snapshot_id": spec.source_snapshot_id,
        "source_snapshot_hash": spec.source_snapshot_hash,
        "source_manifest_hash": spec.source_manifest_hash,
        "cutoff_chapter": spec.cutoff_chapter,
        "divergence_manifest_hash": candidate.divergence_manifest_hash,
        "identity_lineage": identity_lineage,
        "source_refs": source_refs,
        "generator_lineage": dict(candidate.generator_lineage),
        "consistency_evidence": consistency_evidence,
        "consistency_report": report,
        "consistency_verdict": report.get("verdict"),
        "review_state": review_state,
    }


def _candidate_idempotency_key(
    *,
    owner_id: int,
    novel_id: int,
    visual_version_id: int,
    asset_key: str,
    payload_hash: str,
) -> str:
    return canonical_derivative_asset_hash(
        {
            "kind": "derivative_visual_candidate",
            "owner_id": owner_id,
            "novel_id": novel_id,
            "visual_version_id": visual_version_id,
            "asset_key": asset_key,
            "payload_hash": payload_hash,
        }
    )


async def store_derivative_candidate_asset(
    db: AsyncSession,
    storage: DerivativeAssetStorage,
    *,
    owner_id: int,
    novel_id: int,
    spec: DerivativeSceneSpecContract,
    candidate: DerivativeAssetCandidateWrite,
    payload: bytes,
) -> tuple[DerivativeVisualCandidateAsset, bool]:
    """Deterministic write-only store of one candidate asset (D-38-03).

    Replays the frozen spec, requires the approved fork version in scope,
    verifies the checksum / divergence manifest / identity / source lineage,
    computes the cross-chapter consistency signal and persists the immutable
    candidate row. A duplicate ``asset_key`` with identical content replays; a
    conflicting retry fails closed. The Original rows are never touched.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    if spec.owner_id != owner_id or spec.novel_id != novel_id:
        raise DerivativeCandidateScopeError(
            "frozen Scene Spec scope does not match the request scope"
        )
    try:
        # Defense-in-depth: re-validate the frozen spec so its content_hash
        # replays from its canonical payload (a tampered spec fails closed).
        spec = DerivativeSceneSpecContract.model_validate(spec.model_dump())
    except ValidationError as exc:
        raise DerivativeCandidateConflict(
            "scene_spec_invalid", f"frozen derivative Scene Spec failed its own gate: {exc}"
        ) from exc
    if candidate.scene_spec_hash != spec.content_hash:
        raise DerivativeCandidateConflict(
            "scene_spec_hash_mismatch",
            "candidate scene_spec_hash does not match the frozen Scene Spec",
        )
    if spec.visual_namespace != DERIVATIVE_ASSET_NAMESPACE:
        raise DerivativeCandidateConflict(
            "namespace_denied",
            f"only the {DERIVATIVE_ASSET_NAMESPACE!r} namespace is a derivative "
            "asset storage target",
        )

    version = await db.scalar(
        select(DerivativeVisualVersion).where(
            DerivativeVisualVersion.owner_id == owner_id,
            DerivativeVisualVersion.novel_id == novel_id,
            DerivativeVisualVersion.id == spec.visual_fork_version_id,
        )
    )
    if version is None:
        raise DerivativeCandidateScopeError(
            "derivative visual fork version not found in the owner/novel scope"
        )
    if version.review_state != "approved":
        raise DerivativeCandidateConflict(
            "visual_fork_not_approved",
            "only an approved derivative visual fork can anchor candidate assets",
        )
    if version.canonical_payload_hash != spec.visual_fork_version_hash:
        raise DerivativeCandidateConflict(
            "visual_fork_version_hash_mismatch",
            "candidate visual fork version hash does not replay the approved fork",
        )
    if (
        version.source_snapshot_hash != spec.source_snapshot_hash
        or version.source_manifest_hash != spec.source_manifest_hash
    ):
        raise DerivativeCandidateConflict(
            "source_snapshot_hash_mismatch",
            "candidate source snapshot lineage does not match the approved fork",
        )

    # Content checksum: always replay from the bytes; a mismatch fails closed.
    if not payload:
        raise DerivativeCandidateConflict("empty_payload", "cannot store an empty asset")
    if len(payload) > MAX_DERIVATIVE_ASSET_BYTES:
        raise DerivativeCandidateConflict(
            "payload_too_large",
            f"candidate payload exceeds the {MAX_DERIVATIVE_ASSET_BYTES} byte limit",
        )
    actual_hash = hashlib.sha256(payload).hexdigest()
    if actual_hash != candidate.content_hash:
        raise DerivativeCandidateConflict(
            "content_hash_mismatch",
            "candidate content_hash does not replay from the uploaded bytes",
        )

    # Divergence manifest: the claimed hash must replay the fork's divergence.
    expected_div_hash = divergence_manifest_hash_from_spec(spec)
    if candidate.divergence_manifest_hash != expected_div_hash:
        raise DerivativeCandidateConflict(
            "divergence_manifest_hash_mismatch",
            "candidate divergence_manifest_hash does not replay the fork "
            "divergence declaration (D-38-02)",
        )

    # Identity lineage must pin the exact spec identity rows (drift blocked).
    expected_identity = _spec_identity_rows(spec)
    if not expected_identity:
        raise DerivativeCandidateConflict(
            "identity_lineage_missing",
            "the frozen Scene Spec carries no identity; a candidate asset "
            "must be bound to at least one identity (D-38-03)",
        )
    claimed_identity = [row.model_dump(mode="json") for row in candidate.identity_lineage]
    if claimed_identity != expected_identity:
        raise DerivativeCandidateConflict(
            "identity_lineage_mismatch",
            "candidate identity lineage does not match the frozen Scene Spec "
            "identity (identity drift is blocked)",
        )
    # Source refs must pin the exact spec reference assets (mixed authority).
    expected_refs = _spec_source_refs(spec)
    claimed_refs = [row.model_dump(mode="json") for row in candidate.source_refs]
    if claimed_refs != expected_refs:
        raise DerivativeCandidateConflict(
            "source_refs_mismatch",
            "candidate source refs do not match the frozen Scene Spec "
            "reference assets (REQ-FORK-04)",
        )

    # Idempotent replay: same asset_key + same content replays the existing row.
    existing = await _candidate_by_key(
        db, owner_id, novel_id, version.id, candidate.asset_key
    )
    if existing is not None:
        if existing.content_hash == actual_hash:
            return existing, True
        raise DerivativeCandidateConflict(
            "duplicate_candidate_conflict",
            f"candidate asset_key {candidate.asset_key!r} already exists with "
            "different content; a conflicting retry cannot replay",
        )

    asset_id = generate_derivative_asset_id()
    storage_key = storage.store(
        owner_id=owner_id,
        novel_id=novel_id,
        visual_version_id=version.id,
        asset_id=asset_id,
        mime_type=candidate.mime_type,
        payload=payload,
    )

    evidence = chapter_evidence_from_spec(spec, candidate.chapter_number)
    siblings = await _sibling_evidence(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        visual_version_id=version.id,
        identity_key=evidence.identity_key,
        exclude_storage_key=storage_key,
    )
    report = score_cross_chapter_consistency(
        tuple(siblings) + (evidence,)
    )
    review_state = review_state_from_consistency_verdict(report.verdict).value
    identity_lineage = [
        DerivativeAssetIdentityRow.model_validate(row).model_dump(mode="json")
        for row in expected_identity
    ]
    source_refs = [
        DerivativeAssetSourceRef.model_validate(row).model_dump(mode="json")
        for row in expected_refs
    ]
    payload_dict = _candidate_payload(
        owner_id=owner_id,
        novel_id=novel_id,
        version=version,
        spec=spec,
        candidate=candidate,
        asset_id=asset_id,
        storage_key=storage_key,
        content_hash=actual_hash,
        identity_lineage=identity_lineage,
        source_refs=source_refs,
        consistency_evidence=evidence.model_dump(mode="json"),
        report=report.model_dump(mode="json"),
        review_state=review_state,
    )
    payload_hash = canonical_derivative_asset_hash(payload_dict)
    row = DerivativeVisualCandidateAsset(
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=version.project_id,
        fork_id=version.fork_id,
        visual_version_id=version.id,
        visual_version_hash=version.canonical_payload_hash,
        version_key=version.version_key,
        asset_key=candidate.asset_key,
        asset_id=asset_id,
        storage_key=storage_key,
        mime_type=candidate.mime_type,
        content_hash=actual_hash,
        size_bytes=len(payload),
        visual_namespace=DERIVATIVE_ASSET_NAMESPACE,
        scene_spec_hash=spec.content_hash,
        chapter_number=candidate.chapter_number,
        source_snapshot_id=spec.source_snapshot_id,
        source_snapshot_hash=spec.source_snapshot_hash,
        source_manifest_hash=spec.source_manifest_hash,
        cutoff_chapter=spec.cutoff_chapter,
        identity_key=evidence.identity_key,
        identity_lineage=identity_lineage,
        source_refs=source_refs,
        generator_lineage=dict(candidate.generator_lineage),
        divergence_manifest_hash=candidate.divergence_manifest_hash,
        consistency_evidence=evidence.model_dump(mode="json"),
        consistency_report=report.model_dump(mode="json"),
        consistency_verdict=report.verdict.value,
        review_state=review_state,
        canonical_payload=payload_dict,
        canonical_payload_hash=payload_hash,
        idempotency_key=_candidate_idempotency_key(
            owner_id=owner_id,
            novel_id=novel_id,
            visual_version_id=version.id,
            asset_key=candidate.asset_key,
            payload_hash=payload_hash,
        ),
        projection_hash=canonical_derivative_asset_hash(
            {
                "consistency_verdict": report.verdict.value,
                "review_state": review_state,
                "consistency_report": report.model_dump(mode="json"),
            }
        ),
        schema_version=DERIVATIVE_ASSET_SCHEMA_VERSION,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await _candidate_by_key(
            db, owner_id, novel_id, version.id, candidate.asset_key
        )
        if existing is None:
            raise DerivativeCandidateConflict(
                "candidate_race",
                "candidate create race; existing row not found after rollback",
            ) from None
        if existing.content_hash == actual_hash:
            return existing, True
        raise DerivativeCandidateConflict(
            "duplicate_candidate_conflict",
            "candidate asset_key was created concurrently with different content",
        ) from None
    return row, False


# ---------------------------------------------------------------------------
# Review lineage (append-only, explicit, idempotent)
# ---------------------------------------------------------------------------


async def apply_derivative_asset_review(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    event: DerivativeAssetReviewEventInput,
) -> DerivativeVisualCandidateAsset:
    """Append one explicit review action; only the review_state projection moves.

    A repeated ``event_key`` replays without a second event; a ``blocked``
    candidate (identity drift / undeclared divergence) can never be approved.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    if event.owner_id != owner_id or event.novel_id != novel_id:
        raise DerivativeCandidateScopeError(
            "review event scope does not match request scope"
        )
    candidate = await db.scalar(
        select(DerivativeVisualCandidateAsset).where(
            DerivativeVisualCandidateAsset.owner_id == owner_id,
            DerivativeVisualCandidateAsset.novel_id == novel_id,
            DerivativeVisualCandidateAsset.id == event.candidate_id,
        )
    )
    if candidate is None:
        raise DerivativeCandidateScopeError(
            "derivative candidate asset not found in the owner/novel scope"
        )
    if candidate.review_state != event.from_review_state.value:
        raise DerivativeAssetReviewError(
            f"review from_review_state {event.from_review_state.value!r} does not "
            f"match the candidate's current state {candidate.review_state!r}"
        )

    existing = await db.scalar(
        select(DerivativeVisualCandidateReviewEvent).where(
            DerivativeVisualCandidateReviewEvent.owner_id == owner_id,
            DerivativeVisualCandidateReviewEvent.novel_id == novel_id,
            DerivativeVisualCandidateReviewEvent.candidate_id == candidate.id,
            DerivativeVisualCandidateReviewEvent.event_key == event.event_key,
        )
    )
    if existing is not None:
        return candidate  # idempotent replay: no second event, no state change

    try:
        to_state = derivative_asset_review_state_after(
            event.from_review_state, event.action
        )
    except ValueError as exc:
        raise DerivativeAssetReviewError(str(exc)) from exc

    review_row = DerivativeVisualCandidateReviewEvent(
        owner_id=owner_id,
        novel_id=novel_id,
        candidate_id=candidate.id,
        action=event.action.value,
        actor_source=event.actor_source,
        actor=event.actor,
        reason=event.reason,
        event_key=event.event_key,
        from_review_state=event.from_review_state.value,
        to_review_state=to_state.value,
        details=event.details or {},
    )
    db.add(review_row)
    candidate.review_state = to_state.value
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        candidate = await db.scalar(
            select(DerivativeVisualCandidateAsset).where(
                DerivativeVisualCandidateAsset.owner_id == owner_id,
                DerivativeVisualCandidateAsset.novel_id == novel_id,
                DerivativeVisualCandidateAsset.id == event.candidate_id,
            )
        )
        if candidate is None:
            raise DerivativeCandidateScopeError(
                "derivative candidate asset disappeared during review replay"
            ) from None
        return candidate
    return candidate


# ---------------------------------------------------------------------------
# Read seams (owner-scoped)
# ---------------------------------------------------------------------------


async def load_candidate(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    candidate_id: int,
) -> DerivativeVisualCandidateAsset:
    candidate = await _candidate_by_id(db, owner_id, novel_id, candidate_id)
    if candidate is None:
        raise DerivativeCandidateScopeError(
            "derivative candidate asset not found in the owner/novel scope"
        )
    return candidate


async def _candidate_by_id(
    db: AsyncSession, owner_id: int, novel_id: int, candidate_id: int
) -> DerivativeVisualCandidateAsset | None:
    return await db.scalar(
        select(DerivativeVisualCandidateAsset).where(
            DerivativeVisualCandidateAsset.owner_id == owner_id,
            DerivativeVisualCandidateAsset.novel_id == novel_id,
            DerivativeVisualCandidateAsset.id == candidate_id,
        )
    )


async def _candidate_by_key(
    db: AsyncSession,
    owner_id: int,
    novel_id: int,
    visual_version_id: int,
    asset_key: str,
) -> DerivativeVisualCandidateAsset | None:
    return await db.scalar(
        select(DerivativeVisualCandidateAsset).where(
            DerivativeVisualCandidateAsset.owner_id == owner_id,
            DerivativeVisualCandidateAsset.novel_id == novel_id,
            DerivativeVisualCandidateAsset.visual_version_id == visual_version_id,
            DerivativeVisualCandidateAsset.asset_key == asset_key,
        )
    )


async def _sibling_evidence(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    visual_version_id: int,
    identity_key: str,
    exclude_storage_key: str,
) -> list:
    """Existing same-identity candidate evidence for the cross-chapter score."""
    rows = (
        await db.scalars(
            select(DerivativeVisualCandidateAsset)
            .where(
                DerivativeVisualCandidateAsset.owner_id == owner_id,
                DerivativeVisualCandidateAsset.novel_id == novel_id,
                DerivativeVisualCandidateAsset.visual_version_id == visual_version_id,
                DerivativeVisualCandidateAsset.identity_key == identity_key,
                DerivativeVisualCandidateAsset.storage_key != exclude_storage_key,
            )
            .order_by(DerivativeVisualCandidateAsset.chapter_number.asc())
        )
    ).all()
    return [
        _evidence_from_row(row) for row in rows if row.consistency_evidence
    ]


def _evidence_from_row(row: DerivativeVisualCandidateAsset):
    from app.schemas.derivative_visual_asset import ChapterConsistencyEvidence

    return ChapterConsistencyEvidence.model_validate(dict(row.consistency_evidence or {}))


__all__ = [
    "ALLOWED_DERIVATIVE_MIME_TYPES",
    "DERIVATIVE_ASSET_SCOPE_PREFIX",
    "DERIVATIVE_ASSET_NAMESPACE",
    "DerivativeAssetNotFound",
    "DerivativeAssetReviewError",
    "DerivativeAssetStorage",
    "DerivativeAssetStorageError",
    "DerivativeCandidateConflict",
    "DerivativeCandidateScopeError",
    "MAX_DERIVATIVE_ASSET_BYTES",
    "apply_derivative_asset_review",
    "generate_derivative_asset_id",
    "load_candidate",
    "store_derivative_candidate_asset",
]
