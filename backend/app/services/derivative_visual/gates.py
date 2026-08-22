"""Deterministic derivative Scene Spec gates (Phase 38-02, D-38-01/02/03).

REQ-FORK-04 / REQ-CRE-06: before a canonical derivative Scene Spec may be
compiled — and before any provider call — the sealed story context and the
approved visual fork must pass a fixed set of deterministic gates. A failing
gate **blocks the compile** with a stable machine reason code; nothing is ever
silently repaired, repointed or promoted.

Gate vocabulary (stable reason codes, 38-VALIDATION failure policy):
``upstream``, ``namespace``, ``source_hash``, ``cutoff``, ``divergence``,
``identity``, ``mixed_authority``, ``implicit_canon``.

- ``upstream``: every referenced contract (approved fork version, approved
  Original Visual Bible revision, approved SceneSpec + its contract) exists in
  scope and is approved; a missing upstream contract blocks the compile.
- ``namespace``: the derivative namespace is sealed to ``fanfiction_visual``
  and the source manifest hash binds to the Original snapshot (wrong namespace
  / wrong source manifest are rejected).
- ``source_hash``: the fork's source snapshot/manifest hashes, the Original
  Visual Bible revision and the sealed SceneSpec must replay the exact same
  snapshot lineage (stale/mismatched source hash fails closed).
- ``cutoff``: the scene spec cutoff must equal the frozen fork cutoff and no
  identity disclosure or evidence may exceed it (future cutoff is rejected).
- ``divergence``: an explicit non-empty divergence is required at the version
  and per-identity level (hidden divergence is rejected).
- ``identity``: stable ids are unique and every identity row pins the exact
  Original entity (source_entity_ref with hash) — identity drift is rejected.
- ``mixed_authority``: every reference asset carries its Original asset hash,
  no derivative asset is silently approved, and asset/anchor/export-manifest
  refs are in the closed vocabulary (mixed Original/Derivative authority and
  reused original paths are rejected).
- ``implicit_canon``: the sealed SceneSpec replays its own contract and its
  unresolved items are carried, so an unbacked detail can never be rendered as
  canon (unsupported / implicit canon details are rejected).

This module is DB-free: all gates operate on the frozen
``DerivativeSceneSpecCompileInput`` so they are unit-testable without
PostgreSQL. Nothing here writes to the database and nothing calls a provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.schemas.derivative_visual import (
    DERIVATIVE_VISUAL_NAMESPACE,
    DerivativeAnchorRef,
    DerivativeAssetLineageRow,
    DerivativeIdentityRow,
    DerivativeNegativeConstraint,
    DerivativeReferenceAssetRow,
    DerivativeSceneSpecEvidenceRef,
    DerivativeSceneSpecUncertainty,
)
from app.schemas.scene_spec import (
    SceneSpecContract,
    SceneSpecGateError,
    validate_scene_spec_contract,
)

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Closed approval vocabularies mirrored from the ORM contracts.
DERIVATIVE_ASSET_LINEAGE_STATES = (
    "candidate",
    "proposal_ready",
    "rejected",
    "superseded",
)
DERIVATIVE_ANCHOR_PUBLISHED_STATUSES = ("valid", "needs_repair", "invalid")

# Closed gate names in evaluation order.
SPEC_GATE_ORDER = (
    "upstream",
    "namespace",
    "source_hash",
    "cutoff",
    "divergence",
    "identity",
    "mixed_authority",
    "implicit_canon",
)


class DerivativeSceneSpecGateError(ValueError):
    """Fail-closed derivative Scene Spec gate violation.

    ``checks`` carries the full gate report so an API can return an auditable
    blocked envelope instead of a bare 4xx.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        gate: str | None = None,
        checks: list["GateCheck"] | None = None,
    ) -> None:
        self.code = code
        self.detail = detail
        self.gate = gate
        self.checks = list(checks or [])
        super().__init__(f"{code}: {detail}")


class DerivativeSceneSpecBlockedError(DerivativeSceneSpecGateError):
    """Fail-closed blocking when an upstream contract is missing or unapproved.

    The compile is blocked before any gate runs — no spec is produced and no
    provider is ever called (``上游契约缺失则 blocked``).
    """


class DerivativeSceneSpecScopeError(ValueError):
    """A contract is outside the explicit owner/novel scope (404-equivalent)."""


@dataclass(frozen=True)
class GateCheck:
    """One deterministic gate result with a stable machine reason code."""

    gate: str
    code: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class DerivativeSceneSpecCompileInput:
    """Frozen server-verified compile request (no ORM objects, DB-free gates).

    Every field is a plain value, contract row or the sealed SceneSpec
    contract. Scope/lineage values are server-derived; the client can never
    supply an owner/novel/fork/namespace/approval.
    """

    owner_id: int
    novel_id: int
    project_id: int
    fork_id: int
    spec_key: str
    # Approved derivative visual fork revision (38-01).
    visual_fork_version_id: int
    visual_fork_version_hash: str
    visual_fork_review_state: str
    visual_namespace: str
    divergence: dict[str, Any]
    provenance: dict[str, Any]
    cutoff_chapter: int
    # Approved Original Visual Bible revision (read-only source snapshot).
    visual_bible_revision_id: int
    visual_bible_revision_hash: str
    visual_bible_review_state: str
    source_snapshot_id: str
    source_snapshot_hash: str
    source_manifest_hash: str
    revision_number: int = 1
    style_profile: dict[str, Any] | None = None
    identity: tuple[DerivativeIdentityRow, ...] = ()
    reference_assets: tuple[DerivativeReferenceAssetRow, ...] = ()
    derivative_negative_constraints: tuple[DerivativeNegativeConstraint, ...] = ()
    # Sealed story context: the approved original SceneSpec.
    scene_spec_id: int | None = None
    scene_spec_hash: str = ""
    scene_spec_review_state: str = "candidate"
    scene_spec_visual_bible_revision_hash: str = ""
    scene_spec_source_snapshot_hash: str = ""
    scene_spec_cutoff_chapter: int = 1
    scene_spec: SceneSpecContract | None = None
    evidence_refs: tuple[DerivativeSceneSpecEvidenceRef, ...] = ()
    uncertainties: tuple[DerivativeSceneSpecUncertainty, ...] = ()
    # AssetRevision / anchor / export-manifest lineage (approved-only).
    asset_lineage: tuple[DerivativeAssetLineageRow, ...] = ()
    anchors: tuple[DerivativeAnchorRef, ...] = ()
    export_manifest_hash: str | None = None


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and bool(HEX64_RE.match(value))


def _ok(gate: str, detail: str = "") -> GateCheck:
    return GateCheck(gate=gate, code="ok", ok=True, detail=detail)


def _fail(gate: str, code: str, detail: str) -> GateCheck:
    return GateCheck(gate=gate, code=code, ok=False, detail=detail)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def evaluate_upstream_gate(input_: DerivativeSceneSpecCompileInput) -> GateCheck:
    """Every referenced contract must exist in scope and be approved."""
    if input_.visual_fork_version_id <= 0 or not _is_hex64(
        input_.visual_fork_version_hash
    ):
        return _fail(
            "upstream",
            "visual_fork_lineage_missing",
            "the approved derivative visual fork revision lineage is incomplete",
        )
    if input_.visual_fork_review_state != "approved":
        return _fail(
            "upstream",
            "visual_fork_not_approved",
            "only an approved derivative visual fork can anchor a Scene Spec",
        )
    if input_.visual_bible_revision_id <= 0 or not _is_hex64(
        input_.visual_bible_revision_hash
    ):
        return _fail(
            "upstream",
            "visual_bible_revision_missing",
            "the approved Original Visual Bible revision lineage is incomplete",
        )
    if input_.visual_bible_review_state != "approved":
        return _fail(
            "upstream",
            "visual_bible_source_not_approved",
            "only an approved Original Visual Bible revision can be forked from",
        )
    if (
        input_.scene_spec_id is None
        or input_.scene_spec_id <= 0
        or not _is_hex64(input_.scene_spec_hash)
    ):
        return _fail(
            "upstream",
            "scene_spec_missing",
            "the sealed story context SceneSpec is missing",
        )
    if input_.scene_spec_review_state != "approved":
        return _fail(
            "upstream",
            "scene_spec_not_approved",
            "only an approved SceneSpec can be sealed into a derivative Scene Spec",
        )
    if input_.scene_spec is None:
        return _fail(
            "upstream",
            "scene_spec_contract_missing",
            "the sealed SceneSpec contract was not provided for revalidation",
        )
    return _ok("upstream")


def evaluate_namespace_gate(input_: DerivativeSceneSpecCompileInput) -> GateCheck:
    """Sealed derivative namespace and exact Original manifest binding."""
    if input_.visual_namespace != DERIVATIVE_VISUAL_NAMESPACE:
        return _fail(
            "namespace",
            "namespace_denied",
            f"only the {DERIVATIVE_VISUAL_NAMESPACE!r} namespace is a derivative "
            "Scene Spec target; Original Canon / user interpretation can never "
            "be compiled here",
        )
    if input_.source_manifest_hash != input_.visual_bible_revision_hash:
        return _fail(
            "namespace",
            "source_manifest_hash_mismatch",
            "the derivative fork source_manifest_hash does not match the approved "
            "Original Visual Bible revision it is forked from",
        )
    return _ok("namespace")


def evaluate_source_hash_gate(input_: DerivativeSceneSpecCompileInput) -> GateCheck:
    """Fork, Original revision and sealed SceneSpec share one snapshot lineage."""
    if input_.source_snapshot_hash != input_.scene_spec_source_snapshot_hash:
        return _fail(
            "source_hash",
            "source_snapshot_hash_mismatch",
            "the derivative fork and the sealed SceneSpec must be frozen against "
            "the same original source snapshot (stale source hash)",
        )
    if (
        input_.scene_spec_visual_bible_revision_hash
        != input_.visual_bible_revision_hash
    ):
        return _fail(
            "source_hash",
            "scene_spec_visual_bible_revision_mismatch",
            "the sealed SceneSpec was compiled against a different Original Visual "
            "Bible revision than the fork references",
        )
    if (
        input_.scene_spec is not None
        and input_.scene_spec.content_hash != input_.scene_spec_hash
    ):
        return _fail(
            "source_hash",
            "scene_spec_hash_mismatch",
            "the sealed SceneSpec content_hash does not replay from its contract",
        )
    return _ok("source_hash")


def evaluate_cutoff_gate(input_: DerivativeSceneSpecCompileInput) -> GateCheck:
    """Frozen fork cutoff; no identity disclosure or evidence beyond it."""
    if input_.scene_spec_cutoff_chapter != input_.cutoff_chapter:
        return _fail(
            "cutoff",
            "cutoff_exceeds_scope",
            f"sealed SceneSpec cutoff {input_.scene_spec_cutoff_chapter} does not "
            f"match the frozen fork cutoff {input_.cutoff_chapter}; a future "
            "cutoff cannot expand the derivative scope",
        )
    for row in input_.identity:
        if row.disclosure_cutoff > input_.cutoff_chapter:
            return _fail(
                "cutoff",
                "identity_disclosure_beyond_cutoff",
                f"identity {row.stable_id!r} disclosure_cutoff "
                f"{row.disclosure_cutoff} exceeds the frozen cutoff "
                f"{input_.cutoff_chapter}",
            )
    for ref in input_.evidence_refs:
        if (
            ref.chapter_number > input_.cutoff_chapter
            or ref.cutoff_chapter != input_.cutoff_chapter
        ):
            return _fail(
                "cutoff",
                "evidence_beyond_cutoff",
                f"evidence {ref.evidence_key!r} is beyond the frozen cutoff "
                f"{input_.cutoff_chapter}",
            )
    return _ok("cutoff")


def evaluate_divergence_gate(input_: DerivativeSceneSpecCompileInput) -> GateCheck:
    """Explicit divergence is mandatory (D-38-02); hidden divergence is blocked."""
    if not input_.divergence:
        return _fail(
            "divergence",
            "divergence_required",
            "a derivative Scene Spec must declare explicit divergence (D-38-02)",
        )
    for row in input_.identity:
        if not row.divergence:
            return _fail(
                "divergence",
                "identity_divergence_required",
                f"identity {row.stable_id!r} must declare explicit divergence; "
                "an un-declared style/identity drift cannot enter the spec",
            )
    return _ok("divergence")


def evaluate_identity_gate(input_: DerivativeSceneSpecCompileInput) -> GateCheck:
    """Unique stable ids and exact Original entity pins (identity drift)."""
    stable_ids = [row.stable_id for row in input_.identity]
    if len(set(stable_ids)) != len(stable_ids):
        return _fail(
            "identity",
            "duplicate_stable_id",
            "derivative Scene Spec identity rows must use unique stable ids",
        )
    for row in input_.identity:
        ref = row.source_entity_ref or {}
        if (
            not ref.get("source_entity_id")
            or not ref.get("source_entity_key")
            or not _is_hex64(str(ref.get("source_entity_hash")))
        ):
            return _fail(
                "identity",
                "identity_source_ref_missing",
                f"identity {row.stable_id!r} must pin the exact Original Visual "
                "Bible entity (source_entity_ref with a 64-hex content hash)",
            )
    return _ok("identity")


def evaluate_mixed_authority_gate(
    input_: DerivativeSceneSpecCompileInput,
) -> GateCheck:
    """Derivative-only authority; no Original path reuse and no silent approval."""
    for asset in input_.reference_assets:
        ref = asset.source_asset_ref or {}
        if not ref.get("source_asset_id") or not _is_hex64(
            str(ref.get("source_bytes_hash"))
        ):
            return _fail(
                "mixed_authority",
                "asset_source_ref_missing",
                f"reference asset {asset.asset_key!r} must pin the exact Original "
                "asset (source_asset_ref with a 64-hex bytes hash)",
            )
        if asset.approved is not False:
            return _fail(
                "mixed_authority",
                "derivative_asset_approved",
                f"reference asset {asset.asset_key!r} is marked approved; a "
                "derivative asset can never be silently canon (D-38-03)",
            )
    for row in input_.asset_lineage:
        if row.approval_state not in DERIVATIVE_ASSET_LINEAGE_STATES:
            return _fail(
                "mixed_authority",
                "asset_lineage_unsupported_state",
                f"asset revision {row.asset_revision_id} carries unsupported "
                f"approval_state {row.approval_state!r}",
            )
        if (
            not _is_hex64(row.bytes_hash)
            or row.scene_spec_hash != input_.scene_spec_hash
        ):
            return _fail(
                "mixed_authority",
                "asset_lineage_spec_mismatch",
                f"asset revision {row.asset_revision_id} is not bound to the "
                "sealed SceneSpec content hash",
            )
    for ref in input_.anchors:
        if ref.status not in DERIVATIVE_ANCHOR_PUBLISHED_STATUSES or not _is_hex64(
            ref.publish_manifest_hash
        ):
            return _fail(
                "mixed_authority",
                "anchor_ref_invalid",
                f"anchor {ref.anchor_key!r} carries an unsupported status or "
                "manifest hash",
            )
    if input_.export_manifest_hash is not None and not _is_hex64(
        input_.export_manifest_hash
    ):
        return _fail(
            "mixed_authority",
            "export_manifest_hash_invalid",
            "export_manifest_hash must be a 64-hex replayable hash",
        )
    return _ok("mixed_authority")


def evaluate_implicit_canon_gate(
    input_: DerivativeSceneSpecCompileInput,
) -> GateCheck:
    """The sealed SceneSpec replays its contract and uncertainties are carried.

    An unbacked/unsupported detail that would leak implicit canon into the
    derivative spec fails closed; unresolved material is carried separately and
    can never be rendered as a positive canon section.
    """
    spec = input_.scene_spec
    if spec is None:
        return _fail(
            "implicit_canon",
            "scene_spec_contract_missing",
            "the sealed SceneSpec contract is required to revalidate canon detail",
        )
    try:
        validate_scene_spec_contract(spec)
    except SceneSpecGateError as exc:
        return _fail(
            "implicit_canon",
            "implicit_canon_detail",
            f"sealed SceneSpec failed revalidation (unsupported or unbacked canon "
            f"detail): {exc}",
        )
    if spec.uncertainties:
        carried = {u.uncertainty_key for u in input_.uncertainties}
        expected = {u.uncertainty_key for u in spec.uncertainties}
        if carried != expected:
            return _fail(
                "implicit_canon",
                "uncertainties_dropped",
                "the derivative Scene Spec must carry every SceneSpec uncertainty "
                "so unresolved material can never be rendered as canon",
            )
    return _ok("implicit_canon")


def run_compile_gates(input_: DerivativeSceneSpecCompileInput) -> list[GateCheck]:
    """Evaluate every gate in fixed order and return the full report."""
    return [
        evaluate_upstream_gate(input_),
        evaluate_namespace_gate(input_),
        evaluate_source_hash_gate(input_),
        evaluate_cutoff_gate(input_),
        evaluate_divergence_gate(input_),
        evaluate_identity_gate(input_),
        evaluate_mixed_authority_gate(input_),
        evaluate_implicit_canon_gate(input_),
    ]


def assert_spec_gates_pass(checks: list[GateCheck]) -> None:
    """Raise the first failing gate; a compile is blocked until all pass."""
    for check in checks:
        if not check.ok:
            raise DerivativeSceneSpecGateError(
                check.code,
                check.detail,
                gate=check.gate,
                checks=checks,
            )


__all__ = [
    "DERIVATIVE_ANCHOR_PUBLISHED_STATUSES",
    "DERIVATIVE_ASSET_LINEAGE_STATES",
    "DERIVATIVE_VISUAL_NAMESPACE",
    "DerivativeSceneSpecBlockedError",
    "DerivativeSceneSpecCompileInput",
    "DerivativeSceneSpecGateError",
    "DerivativeSceneSpecScopeError",
    "GateCheck",
    "SPEC_GATE_ORDER",
    "assert_spec_gates_pass",
    "evaluate_cutoff_gate",
    "evaluate_divergence_gate",
    "evaluate_identity_gate",
    "evaluate_implicit_canon_gate",
    "evaluate_mixed_authority_gate",
    "evaluate_namespace_gate",
    "evaluate_source_hash_gate",
    "evaluate_upstream_gate",
    "run_compile_gates",
]
