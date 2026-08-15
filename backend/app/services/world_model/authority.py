"""Four-label authority envelope, conversion gate and disclosure timing.

Phase 27-04 / REQ-WM-04 (D-01, D-05, D-06).

- D-01: ``canon_fact``, ``probable_inference``, ``literary_interpretation`` and
  ``user_interpretation`` stay distinct authorities. ``AuthorityEnvelope``
  carries the label and its disclosure timing as one immutable value; the
  conversion gate rejects any unauthorized relabel so an inference or
  interpretation can never silently serialize as ``canon_fact``.
- D-05: disclosure timing is first-class. ``visible_at`` decides whether a
  reader at a given cutoff may see the claim (``disclosure_cutoff <= cutoff``),
  independently of when the character knew it.
- D-06: Reader Chat / user conversation is never a world-model fact source, and
  ``user_interpretation`` is a protective human override that stays isolated
  from the original candidate projection. ``is_original_candidate`` separates
  the two.

This module is pure and fail-closed: every conversion returns a stable
``ConversionVerdict`` and nothing is relabeled implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.services.world_model.contracts import Authority
from app.services.world_model.knowledge import SourceKind


class ConversionReason(StrEnum):
    """Stable reason codes for authority conversion verdicts (fail-closed)."""

    LABEL_PRESERVED = "label_preserved"
    AUTHORITY_UPGRADE = "authority_upgrade"
    MISSING_APPROVAL = "missing_approval"
    UNAUTHORIZED_CONVERSION = "unauthorized_conversion"
    CHAT_NOT_FACT_SOURCE = "chat_not_fact_source"


@dataclass(frozen=True)
class AuthorityEnvelope:
    """Immutable epistemic authority envelope: label + disclosure timing.

    ``known_at`` is the story-time chapter at which the proposition holds for a
    character; ``disclosure_cutoff`` is the earliest reader cutoff at which it
    may be shown (D-05). ``approved`` records an explicit gate approval (D-01
    canon publication / D-06 user confirmation). ``is_override`` marks a
    protective human override that is isolated from the candidate projection.
    """

    label: Authority
    known_at: int
    disclosure_cutoff: int
    approved: bool
    source_kind: SourceKind = SourceKind.CANON_SOURCE
    is_override: bool = False

    def visible_at(self, cutoff: int) -> bool:
        """D-05 disclosure timing: reader may see the claim at this cutoff."""
        return self.disclosure_cutoff <= cutoff

    def disclosed_after(self, cutoff: int) -> bool:
        """True when the claim is a future fact / hidden knowledge at cutoff."""
        return self.disclosure_cutoff > cutoff

    @property
    def is_chat_sourced(self) -> bool:
        """D-06: Reader Chat and user conversation are never fact sources."""
        return self.source_kind in (
            SourceKind.READER_CHAT,
            SourceKind.USER_CONVERSATION,
        )

    @property
    def is_original_candidate(self) -> bool:
        """True only for the original candidate projection (not an override)."""
        return (not self.is_override) and self.source_kind == SourceKind.CANON_SOURCE


@dataclass(frozen=True)
class ConversionVerdict:
    """Stable fail-closed verdict for one requested authority conversion."""

    ok: bool
    reason: ConversionReason
    source: Authority
    target: Authority
    message: str


def conversion_gate(
    envelope: AuthorityEnvelope,
    target: Authority,
    *,
    approvals: frozenset[Authority] = frozenset(),
) -> ConversionVerdict:
    """Decide whether a relabel to ``target`` is authorized (D-01/D-06).

    Rules (fail-closed):
    1. Preserving the current label always passes.
    2. A Reader Chat / user conversation source can never become anything else —
       it is not a fact source at all (D-06).
    3. Any move to ``canon_fact`` requires the explicit ``canon_fact`` approval;
       otherwise it is an authority upgrade and is rejected.
    4. Any move to ``user_interpretation`` requires the explicit
       ``user_interpretation`` confirmation.
    5. Any other relabel also requires the target authority to be explicitly
       approved for this submission; a silent relabel is rejected.
    """
    if target == envelope.label:
        return ConversionVerdict(
            ok=True,
            reason=ConversionReason.LABEL_PRESERVED,
            source=envelope.label,
            target=target,
            message=f"authority label {envelope.label.value} preserved (D-01)",
        )
    if envelope.is_chat_sourced:
        return ConversionVerdict(
            ok=False,
            reason=ConversionReason.CHAT_NOT_FACT_SOURCE,
            source=envelope.label,
            target=target,
            message=(
                "Reader Chat / user conversation is never a world-model fact "
                "source; it can never be relabeled into any authority (D-06)"
            ),
        )
    if target == Authority.CANON_FACT:
        if Authority.CANON_FACT in approvals:
            return ConversionVerdict(
                ok=True,
                reason=ConversionReason.LABEL_PRESERVED,
                source=envelope.label,
                target=target,
                message=(
                    f"{envelope.label.value} -> canon_fact approved explicitly (D-01)"
                ),
            )
        return ConversionVerdict(
            ok=False,
            reason=ConversionReason.AUTHORITY_UPGRADE,
            source=envelope.label,
            target=target,
            message=(
                "inference / interpretation must never serialize as canon_fact "
                "without explicit approval (D-01)"
            ),
        )
    if envelope.label == Authority.CANON_FACT:
        # A move away from canon_fact is a safe downgrade, never an upgrade.
        return ConversionVerdict(
            ok=True,
            reason=ConversionReason.LABEL_PRESERVED,
            source=envelope.label,
            target=target,
            message=(
                f"canon_fact -> {target.value} is a safe downgrade; no silent "
                "upgrade occurs (D-01)"
            ),
        )
    if target == Authority.USER_INTERPRETATION:
        if Authority.USER_INTERPRETATION in approvals:
            return ConversionVerdict(
                ok=True,
                reason=ConversionReason.LABEL_PRESERVED,
                source=envelope.label,
                target=target,
                message="user_interpretation confirmed explicitly (D-06)",
            )
        return ConversionVerdict(
            ok=False,
            reason=ConversionReason.MISSING_APPROVAL,
            source=envelope.label,
            target=target,
            message="user_interpretation requires explicit confirmation (D-06)",
        )
    if target in approvals:
        return ConversionVerdict(
            ok=True,
            reason=ConversionReason.LABEL_PRESERVED,
            source=envelope.label,
            target=target,
            message=f"relabel to {target.value} approved explicitly",
        )
    return ConversionVerdict(
        ok=False,
        reason=ConversionReason.UNAUTHORIZED_CONVERSION,
        source=envelope.label,
        target=target,
        message=(
            f"relabel from {envelope.label.value} to {target.value} requires "
            "explicit approval for the target authority"
        ),
    )


def preserved_envelope(
    envelope: AuthorityEnvelope,
    target: Authority,
    *,
    approvals: frozenset[Authority] = frozenset(),
) -> AuthorityEnvelope | None:
    """Return the envelope relabeled to ``target`` or ``None`` when rejected.

    The result keeps the same disclosure timing and override isolation; only the
    authority label changes, and only when the conversion gate authorizes it.
    """
    verdict = conversion_gate(envelope, target, approvals=approvals)
    if not verdict.ok:
        return None
    return AuthorityEnvelope(
        label=target,
        known_at=envelope.known_at,
        disclosure_cutoff=envelope.disclosure_cutoff,
        approved=envelope.approved,
        source_kind=envelope.source_kind,
        is_override=envelope.is_override,
    )
