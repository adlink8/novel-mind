"""Deterministic release verifier producing QualifiedChunkerEvidence (07-06)."""

from __future__ import annotations

from typing import Any

from app.services.chunking.builds import InMemoryBuildStore
from app.services.chunking.schemas import QualifiedChunkerEvidence
from app.services.rag_fixture import stable_hash


def verify_and_qualify(
    store: InMemoryBuildStore,
    *,
    ab_report: dict[str, Any],
    candidate_build_id: str,
    policy_hash: str,
) -> QualifiedChunkerEvidence:
    """Fail closed: only emit status=qualified when all gates pass."""
    rec = store.builds.get(candidate_build_id)
    reasons: list[str] = []

    def _reject(*why: str, status: str = "rejected") -> QualifiedChunkerEvidence:
        reasons.extend(why)
        payload = {
            "build_id": candidate_build_id,
            "status": status,
            "reasons": reasons,
        }
        return QualifiedChunkerEvidence(
            build_id=candidate_build_id or "unknown00",
            manifest_checksum=(rec.manifest_checksum if rec else "0" * 64),
            source_snapshot_hash=(rec.source_snapshot_hash if rec else "0" * 64),
            chunker_name=rec.chunker_name if rec else "unknown",
            chunker_version=rec.chunker_version if rec else "0",
            chunker_config_hash=rec.chunker_config_hash if rec else "0" * 64,
            chunk_manifest_hash=rec.manifest_checksum if rec else "0" * 64,
            policy_hash=policy_hash if len(policy_hash) == 64 else stable_hash({"p": policy_hash}),
            quality_comparable=False,
            status=status if status in ("rejected", "blocked") else "rejected",
            report_signature=stable_hash(payload),
            metrics={},
            reasons=reasons,
        )

    if rec is None:
        return _reject("build_missing", status="blocked")
    if not ab_report.get("quality_comparable"):
        return _reject(
            ab_report.get("reason") or "not_comparable",
            status="blocked" if ab_report.get("status") == "blocked_dependency" else "rejected",
        )
    if ab_report.get("source_snapshot_hash") != rec.source_snapshot_hash:
        return _reject("snapshot_mismatch")
    if ab_report.get("policy_hash") != policy_hash:
        return _reject("policy_mismatch")

    mb = (ab_report.get("metrics") or {}).get("B") or ab_report.get("B", {}).get("metrics") or {}
    ma = (ab_report.get("metrics") or {}).get("A") or ab_report.get("A", {}).get("metrics") or {}

    # Gates
    if mb.get("coverage", 0) < 1.0:
        reasons.append("coverage_incomplete")
    if mb.get("overlap", 0) > 0:
        reasons.append("overlap_nonzero")
    if mb.get("critical_false_split", 1) != 0:
        reasons.append("critical_false_split")
    if mb.get("split_f1", 0) < 0.90:
        reasons.append("split_f1_below_floor")
    if ma and mb.get("split_f1", 0) + 1e-9 < ma.get("split_f1", 0):
        reasons.append("split_f1_regression_vs_A")
    if mb.get("scene_coherence_mean", 0) < 4.0:
        reasons.append("scene_coherence_low")
    if ma:
        if ma.get("recall_at_5", 0) - mb.get("recall_at_5", 0) > 0.02 + 1e-9:
            reasons.append("recall_regression")
        if ma.get("mrr", 0) - mb.get("mrr", 0) > 0.03 + 1e-9:
            reasons.append("mrr_regression")
        if ma.get("ndcg", 0) - mb.get("ndcg", 0) > 0.03 + 1e-9:
            reasons.append("ndcg_regression")
        cost_a = float(ma.get("cost_usd_total") or 0)
        cost_b = float(mb.get("cost_usd_total") or 0)
        if cost_a > 0 and cost_b > cost_a * 1.15 + 1e-9:
            reasons.append("cost_over_budget")
    if not ab_report.get("report_signature"):
        reasons.append("missing_report_signature")

    if reasons:
        return _reject(*reasons)

    metrics = dict(mb)
    body = {
        "build_id": rec.build_id,
        "manifest": rec.manifest_checksum,
        "snapshot": rec.source_snapshot_hash,
        "policy": policy_hash,
        "metrics": metrics,
        "status": "qualified",
    }
    sig = stable_hash(body)
    return QualifiedChunkerEvidence(
        build_id=rec.build_id,
        manifest_checksum=rec.manifest_checksum,
        source_snapshot_hash=rec.source_snapshot_hash,
        chunker_name=rec.chunker_name,
        chunker_version=rec.chunker_version,
        chunker_config_hash=rec.chunker_config_hash,
        chunk_manifest_hash=rec.manifest_checksum,
        policy_hash=policy_hash if len(policy_hash) == 64 else stable_hash({"p": policy_hash}),
        quality_comparable=True,
        status="qualified",
        report_signature=sig,
        metrics=metrics,
        reasons=[],
    )
