"""Provider-free consistency checks for a creative context package.

The checker consumes structured claims only. It does not infer facts from
free-form text and it never calls a model or writes a domain state.
"""

from __future__ import annotations

import hashlib
import json

from app.schemas.creative_evaluation import (
    CreativeClaim,
    CreativeConsistencyFinding,
    CreativeConsistencyReport,
)
from app.schemas.creative_generation import CreativeContextPackage
from app.services.creative_generation_policy import validate_context_package


def _hash_payload(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate_consistency(
    package: CreativeContextPackage,
    claims: list[CreativeClaim],
    *,
    owner_id: int,
    novel_id: int,
) -> CreativeConsistencyReport:
    """Evaluate structured claims against package scope and citation rules."""

    validate_context_package(package, owner_id=owner_id, novel_id=novel_id)
    evidence_keys = {ref.evidence_key for ref in package.original_evidence}
    findings: list[CreativeConsistencyFinding] = []
    cited_claims = 0

    for claim in claims:
        if not claim.evidence_keys:
            findings.append(
                CreativeConsistencyFinding(
                    claim_key=claim.claim_key,
                    rule_code="missing_evidence",
                    severity="error",
                    detail="claim has no original-canon evidence citation",
                )
            )
        elif not set(claim.evidence_keys).issubset(evidence_keys):
            findings.append(
                CreativeConsistencyFinding(
                    claim_key=claim.claim_key,
                    rule_code="evidence_outside_package",
                    severity="error",
                    detail="claim cites evidence not present in the context package",
                )
            )
        else:
            cited_claims += 1

        if claim.chapter_number is not None and claim.chapter_number > package.cutoff_chapter_number:
            findings.append(
                CreativeConsistencyFinding(
                    claim_key=claim.claim_key,
                    rule_code="cutoff_exceeded",
                    severity="error",
                    detail="claim is beyond the declared original-canon cutoff",
                )
            )
        if claim.disposition == "contradiction":
            findings.append(
                CreativeConsistencyFinding(
                    claim_key=claim.claim_key,
                    rule_code="contradiction",
                    severity="error",
                    detail="structured claim is marked as contradicting established canon",
                )
            )
        elif claim.disposition == "unknown":
            findings.append(
                CreativeConsistencyFinding(
                    claim_key=claim.claim_key,
                    rule_code="uncertain",
                    severity="warning",
                    detail="claim requires an authorized evidence or model judgment",
                )
            )

    errors = [finding for finding in findings if finding.severity == "error"]
    warnings = [finding for finding in findings if finding.severity == "warning"]
    status = "failed" if errors else "passed_with_warnings" if warnings else "passed"
    report = CreativeConsistencyReport(
        package_hash=package.context_hash,
        checked_claims=len(claims),
        citation_coverage=(cited_claims / len(claims)) if claims else 1.0,
        status=status,
        findings=findings,
        report_hash="0" * 64,
    )
    return report.model_copy(
        update={
            "report_hash": _hash_payload(
                report.model_dump(mode="json", exclude={"report_hash"})
            )
        }
    )
