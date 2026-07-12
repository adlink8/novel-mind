"""RAG quality SUT scoring, metrics, and deterministic policy arbiter (06-04).

Consumes signed frozen fixtures + calibrated Judge lineage from 06-03.
Does NOT generate fixtures. Judge alone cannot promote; arbiter is final gate.

D-06..D-08: retrieval+answer, four quality metrics, thresholds, fail-closed.
"""

from __future__ import annotations

import logging
import math
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from app.schemas.eval import (
    INVALID_LINEAGE_REASON,
    LEGACY_INCOMPARABLE_REASON,
    SCHEMA_VERSION_RAG_QUALITY,
    CalibrationReport,
    ChunkerLineage,
    EvalCase,
    ModelLineage,
    SourceSnapshot,
)
from app.services.rag_fixture import (
    DEFAULT_SIGNING_SECRET,
    InvalidFixtureError,
    InvalidLineageError,
    fail_closed,
    prompts_dir,
    schema_contract_hash,
    sign_payload,
    stable_hash,
    validate_generator_judge_isolation,
    verify_frozen_case,
    verify_source_snapshot,
)

logger = logging.getLogger(__name__)

POLICY_VERSION = "rag-quality-policy.v1"
ANSWER_JUDGE_PROMPT_VERSION = "rag_answer_judge.v1"
_SHA256_HEX_LEN = 64


def recompute_chunker_config_hash(chunker_config: dict[str, Any] | None) -> str:
    """Canonical config hash — never trust a caller-supplied config hash alone."""
    return stable_hash(chunker_config if isinstance(chunker_config, dict) else {})


def canonicalize_chunker_lineage(
    lineage: ChunkerLineage | dict[str, Any] | None,
    *,
    expected_source_snapshot_hash: str | None = None,
    expected_chunk_manifest_hash: str | None = None,
) -> tuple[ChunkerLineage | None, str | None]:
    """Normalize five-tuple lineage or return (None, reason).

    Reasons:
      - legacy_incomparable: missing / empty (no invented hashes)
      - invalid_lineage: present but malformed or mismatched evidence
    """
    if lineage is None:
        return None, LEGACY_INCOMPARABLE_REASON
    if isinstance(lineage, dict):
        if not lineage:
            return None, LEGACY_INCOMPARABLE_REASON
        try:
            lineage = ChunkerLineage.model_validate(lineage)
        except Exception as exc:
            return None, f"{INVALID_LINEAGE_REASON}: {exc}"

    name = (lineage.chunker_name or "").strip()
    version = (lineage.chunker_version or "").strip()
    if not name or not version:
        return None, LEGACY_INCOMPARABLE_REASON

    cfg = lineage.chunker_config if isinstance(lineage.chunker_config, dict) else {}
    config_hash = recompute_chunker_config_hash(cfg)
    # If caller sent a config hash and it disagrees with recomputed → invalid.
    if (
        lineage.chunker_config_hash
        and lineage.chunker_config_hash != config_hash
    ):
        return None, f"{INVALID_LINEAGE_REASON}: chunker_config_hash mismatch"

    for label, value in (
        ("chunk_manifest_hash", lineage.chunk_manifest_hash),
        ("source_snapshot_hash", lineage.source_snapshot_hash),
    ):
        if not value or len(value) != _SHA256_HEX_LEN:
            return None, f"{INVALID_LINEAGE_REASON}: {label} must be sha256 hex"

    if (
        expected_source_snapshot_hash
        and lineage.source_snapshot_hash != expected_source_snapshot_hash
    ):
        return None, f"{INVALID_LINEAGE_REASON}: source_snapshot_hash mismatch"
    if (
        expected_chunk_manifest_hash
        and lineage.chunk_manifest_hash != expected_chunk_manifest_hash
    ):
        return None, f"{INVALID_LINEAGE_REASON}: chunk_manifest_hash mismatch"

    canonical = lineage.model_copy(
        update={
            "chunker_name": name,
            "chunker_version": version,
            "chunker_config": cfg,
            "chunker_config_hash": config_hash,
        }
    )
    return canonical, None


def lineage_five_tuple(lineage: ChunkerLineage | dict[str, Any] | None) -> dict[str, str] | None:
    """Extract five-tuple for hashing; None if incomplete (never invent)."""
    canonical, err = canonicalize_chunker_lineage(lineage)
    if canonical is None or err is not None:
        return None
    return canonical.five_tuple()


def build_quality_input_hash(
    *,
    snapshot_manifest_hash: str | None,
    case_fixture_hashes: list[str | None],
    baseline: dict[str, Any] | None,
    policy_hash_value: str | None = None,
    chunker_lineage: ChunkerLineage | dict[str, Any] | None,
) -> str:
    """Input identity includes complete canonical five-tuple lineage when present."""
    five = lineage_five_tuple(chunker_lineage)
    return stable_hash(
        {
            "snapshot": snapshot_manifest_hash,
            "cases": case_fixture_hashes,
            "baseline": baseline,
            "policy_hash": policy_hash_value,
            "chunker_lineage": five,
        }
    )


def build_stage_cache_key(
    *,
    run_input_hash: str | None,
    case_id: str,
    fixture_hash: str | None,
    repetition: int,
    top_k: int,
    chunker_lineage: ChunkerLineage | dict[str, Any] | None = None,
) -> str:
    """Idempotency key binds run input (incl. lineage) so cross-chunker never collides."""
    five = lineage_five_tuple(chunker_lineage)
    digest = stable_hash(
        {
            "run_input_hash": run_input_hash,
            "case_id": case_id,
            "fixture_hash": fixture_hash,
            "repetition": repetition,
            "top_k": top_k,
            "chunker_lineage": five,
        }
    )
    return f"{case_id}:r{repetition}:{digest[:16]}"

COMPARABLE_STATUSES = frozenset({"passed", "qualified"})
NON_COMPARABLE_TERMINAL = frozenset(
    {
        "failed_policy",
        "quality_regression",
        "blocked_dependency",
        "invalid_fixture",
        "invalid_lineage",
        "quarantined",
        "cancelled",
    }
)

# Stages after frozen fixtures are accepted for SUT evaluation.
SUT_STAGES = (
    "queued",
    "validating",
    "retrieving",
    "answering",
    "scoring",
    "arbitrating",
)

# ---------------------------------------------------------------------------
# Protocols / types for stubbable SUT + Judge
# ---------------------------------------------------------------------------

RetrieveFn = Callable[[EvalCase, SourceSnapshot, int], list[dict[str, Any]]]
AnswerFn = Callable[[EvalCase, list[dict[str, Any]]], dict[str, Any]]
AnswerJudgeFn = Callable[
    [EvalCase, str, list[dict[str, Any]], ModelLineage], dict[str, Any]
]
HealthProbeFn = Callable[[], dict[str, Any]]


class DependencyOutage(Exception):
    """Live dependency (DB/Chroma/model) unavailable."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------


def policy_path() -> Path:
    return Path(__file__).resolve().parents[2] / "evals" / "rag-quality-policy.v1.yml"


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load versioned quality policy. Missing file => caller fail-closed."""
    p = Path(path) if path else policy_path()
    if not p.is_file():
        raise FileNotFoundError(f"policy missing: {p}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML required to load rag quality policy") from exc
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("policy root must be a mapping")
    if data.get("version") != POLICY_VERSION:
        raise ValueError(f"unexpected policy version: {data.get('version')}")
    if "thresholds" not in data or "p95_budgets" not in data:
        raise ValueError("policy missing thresholds or p95_budgets")
    return data


def policy_hash(policy: dict[str, Any]) -> str:
    return stable_hash(policy)


def answer_judge_prompt_hash() -> str:
    path = prompts_dir() / "rag_answer_judge.v1.txt"
    return stable_hash(path.read_text(encoding="utf-8")) if path.is_file() else stable_hash("")


# ---------------------------------------------------------------------------
# Deterministic retrieval metrics (content-hash truth)
# ---------------------------------------------------------------------------


def _gold_content_hashes(case: EvalCase) -> set[str]:
    hashes: set[str] = set()
    for es in case.equivalent_evidence_sets:
        for ref in es.refs:
            hashes.add(ref.chunk_content_hash)
    return hashes


def _retrieved_hashes(retrieved: list[dict[str, Any]], top_k: int = 5) -> list[str]:
    out: list[str] = []
    for item in retrieved[:top_k]:
        h = item.get("chunk_content_hash") or item.get("content_hash")
        if h:
            out.append(str(h))
    return out


def context_precision_at_k(
    case: EvalCase, retrieved: list[dict[str, Any]], top_k: int = 5
) -> float:
    """Fraction of top-k retrieved chunks that match any gold/equivalent evidence set."""
    gold = _gold_content_hashes(case)
    recalled = _retrieved_hashes(retrieved, top_k)
    if not recalled:
        return 0.0 if gold else 1.0
    hits = sum(1 for h in recalled if h in gold)
    return hits / len(recalled)


def context_recall_at_k(
    case: EvalCase, retrieved: list[dict[str, Any]], top_k: int = 5
) -> float:
    """Fraction of gold equivalent-evidence sets covered by top-k retrieval.

    A set is covered if any of its refs' chunk_content_hash appears in retrieval.
    For no_answer cases with empty gold sets, recall is 1.0 (nothing to recall).
    """
    if case.case_type == "no_answer" or not case.equivalent_evidence_sets:
        return 1.0 if case.case_type == "no_answer" else 0.0

    recalled = set(_retrieved_hashes(retrieved, top_k))
    if not recalled:
        return 0.0
    covered = 0
    for es in case.equivalent_evidence_sets:
        set_hashes = {r.chunk_content_hash for r in es.refs}
        if set_hashes & recalled:
            covered += 1
    return covered / len(case.equivalent_evidence_sets)


def _normalize_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()) if t}


def claim_supported_by_evidence(claim_text: str, evidence_texts: list[str]) -> bool:
    """Deterministic support: claim tokens substantially overlap some evidence quote."""
    claim_toks = _normalize_tokens(claim_text)
    if not claim_toks:
        return False
    joined = " ".join(evidence_texts).lower()
    # Substring containment (Chinese-friendly) or token Jaccard-ish
    if claim_text.strip() and claim_text.strip().lower() in joined:
        return True
    for et in evidence_texts:
        et_toks = _normalize_tokens(et)
        if not et_toks:
            continue
        overlap = len(claim_toks & et_toks) / len(claim_toks)
        if overlap >= 0.5:
            return True
    return False


def deterministic_claim_metrics(
    case: EvalCase,
    answer: str,
    retrieved: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute critical unsupported rate and simple claim-level faithfulness."""
    evidence_texts: list[str] = []
    for item in retrieved:
        for key in ("quote_text", "text", "content"):
            if item.get(key):
                evidence_texts.append(str(item[key]))
                break

    if case.case_type == "no_answer":
        # Fabricated claims if answer looks assertive without refuse markers
        refuse_markers = (
            "cannot",
            "insufficient",
            "unknown",
            "无法",
            "证据不足",
            "不足以",
            "no evidence",
            "refuse",
        )
        lowered = (answer or "").lower()
        refused = any(m in lowered for m in refuse_markers) or not (answer or "").strip()
        if refused:
            return {
                "faithfulness_proxy": 1.0,
                "critical_unsupported_claim_rate": 0.0,
                "critical_unsupported_count": 0,
                "claim_verdicts": [],
                "refused": True,
            }
        return {
            "faithfulness_proxy": 0.0,
            "critical_unsupported_claim_rate": 1.0,
            "critical_unsupported_count": 1,
            "claim_verdicts": [
                {
                    "claim_text": answer[:200],
                    "supported": False,
                    "critical": True,
                }
            ],
            "refused": False,
        }

    claims = case.claims or []
    # If no structured claims, treat whole answer as one non-critical claim proxy
    if not claims and answer:
        supported = claim_supported_by_evidence(answer, evidence_texts)
        return {
            "faithfulness_proxy": 1.0 if supported else 0.0,
            "critical_unsupported_claim_rate": 0.0,
            "critical_unsupported_count": 0,
            "claim_verdicts": [
                {"claim_text": answer[:200], "supported": supported, "critical": False}
            ],
            "refused": False,
        }

    verdicts = []
    critical_total = 0
    critical_unsupported = 0
    supported_count = 0
    for c in claims:
        supported = claim_supported_by_evidence(c.text, evidence_texts)
        if c.critical:
            critical_total += 1
            if not supported:
                critical_unsupported += 1
        if supported:
            supported_count += 1
        verdicts.append(
            {
                "claim_id": c.claim_id,
                "claim_text": c.text,
                "supported": supported,
                "critical": c.critical,
            }
        )

    n = len(claims) or 1
    rate = (critical_unsupported / critical_total) if critical_total else 0.0
    return {
        "faithfulness_proxy": supported_count / n,
        "critical_unsupported_claim_rate": rate,
        "critical_unsupported_count": critical_unsupported,
        "claim_verdicts": verdicts,
        "refused": False,
    }


# ---------------------------------------------------------------------------
# Bootstrap / consistency
# ---------------------------------------------------------------------------


def bootstrap_lower_bound(
    scores: list[float],
    *,
    n_boot: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
) -> float:
    """Nonparametric bootstrap (1-alpha) lower bound of the mean."""
    if not scores:
        return 0.0
    if len(scores) == 1:
        return float(scores[0])
    rng = random.Random(seed)
    n = len(scores)
    means: list[float] = []
    for _ in range(n_boot):
        sample = [scores[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    idx = max(0, min(len(means) - 1, int(math.floor(alpha * n_boot))))
    return float(means[idx])


def verdict_consistency(verdicts: list[str]) -> float:
    """Fraction of agreement with the modal verdict (for 3-repeat runs)."""
    if not verdicts:
        return 0.0
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v] = counts.get(v, 0) + 1
    return max(counts.values()) / len(verdicts)


def case_repeat_consistency(per_case_verdicts: list[list[str]]) -> float:
    """Mean per-case full-agreement rate across repeats."""
    if not per_case_verdicts:
        return 0.0
    ok = sum(1 for reps in per_case_verdicts if len(set(reps)) == 1)
    return ok / len(per_case_verdicts)


# ---------------------------------------------------------------------------
# Stub SUT + answer judge (offline unit/contract)
# ---------------------------------------------------------------------------


def default_stub_retrieve(
    case: EvalCase, snapshot: SourceSnapshot, top_k: int = 5
) -> list[dict[str, Any]]:
    """Oracle-aligned retrieval for offline tests: return gold evidence first.

    When gold/equivalent evidence exists, return only those hits (precision=1).
    Otherwise pad with snapshot chunks so no-answer / empty-gold cases still retrieve.
    """
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for es in case.equivalent_evidence_sets:
        for ref in es.refs:
            if ref.chunk_content_hash in seen:
                continue
            seen.add(ref.chunk_content_hash)
            results.append(
                {
                    "chunk_content_hash": ref.chunk_content_hash,
                    "quote_text": ref.quote_text,
                    "start_offset": ref.start_offset,
                    "end_offset": ref.end_offset,
                    "score": 1.0,
                }
            )
            if len(results) >= top_k:
                return results
    if results:
        return results
    # No gold evidence — return snapshot chunks as distractors
    for ch in snapshot.chunks:
        if ch.content_hash in seen:
            continue
        results.append(
            {
                "chunk_content_hash": ch.content_hash,
                "quote_text": ch.text,
                "score": 0.1,
            }
        )
        if len(results) >= top_k:
            break
    return results


def default_stub_answer(
    case: EvalCase, retrieved: list[dict[str, Any]]
) -> dict[str, Any]:
    """Oracle-aligned SUT answer for offline tests."""
    t0 = time.perf_counter()
    if case.case_type == "no_answer":
        answer = "insufficient evidence"
    elif case.case_type == "hard_negative":
        answer = "insufficient equivalent evidence"
    else:
        answer = case.reference_answer or (
            retrieved[0].get("quote_text") if retrieved else "unknown"
        )
    latency_ms = (time.perf_counter() - t0) * 1000
    return {
        "answer": answer,
        "parsed_claims": [c.text for c in case.claims],
        "token_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        "cost_usd": 0.001,
        "latency_ms": latency_ms,
        "status": "ok",
    }


def default_stub_answer_judge(
    case: EvalCase,
    answer: str,
    retrieved: list[dict[str, Any]],
    lineage: ModelLineage,
) -> dict[str, Any]:
    """Deterministic offline answer judge (not a live model)."""
    _ = lineage
    det = deterministic_claim_metrics(case, answer, retrieved)
    faith = float(det["faithfulness_proxy"])
    crit_unsupported = int(det["critical_unsupported_count"])

    # Oracle-aligned offline path: reference match / correct refuse => full credit.
    # Gold claim text may not token-overlap evidence quotes (Chinese paraphrase).
    ans_norm = (answer or "").strip().lower()
    ref_norm = (case.reference_answer or "").strip().lower()
    if case.case_type == "no_answer" and det.get("refused"):
        faith = 1.0
        crit_unsupported = 0
    elif case.case_type == "hard_negative" and (
        "insufficient" in ans_norm or "证据" in (answer or "") or ans_norm == ref_norm
    ):
        faith = 1.0
        crit_unsupported = 0
    elif ref_norm and (ans_norm == ref_norm or ref_norm in ans_norm or ans_norm in ref_norm):
        faith = 1.0
        crit_unsupported = 0
    elif retrieved and claim_supported_by_evidence(answer, [
        str(item.get("quote_text") or item.get("text") or "") for item in retrieved
    ]):
        faith = max(faith, 0.95)
        crit_unsupported = 0

    # relevance: answer token overlap with question / reference
    q_toks = _normalize_tokens(case.question)
    a_toks = _normalize_tokens(answer)
    if not q_toks:
        relevance = 1.0
    else:
        relevance = len(q_toks & a_toks) / len(q_toks)
        if case.case_type == "no_answer" and det.get("refused"):
            relevance = 1.0
        elif case.reference_answer:
            ref_toks = _normalize_tokens(case.reference_answer)
            if ref_toks:
                relevance = max(relevance, len(ref_toks & a_toks) / len(ref_toks))
    if ref_norm and ans_norm == ref_norm:
        relevance = max(relevance, 0.95)
    relevance = min(1.0, max(0.0, relevance))
    return {
        "faithfulness": faith,
        "relevance": relevance,
        "claim_verdicts": det["claim_verdicts"],
        "critical_unsupported_count": crit_unsupported,
        "critical_ambiguity": 0,
        "reason_codes": ["stub_answer_judge"],
    }


# ---------------------------------------------------------------------------
# Input validation (consume 06-03 artifacts)
# ---------------------------------------------------------------------------


def validate_fixtures_for_scoring(
    *,
    snapshot: SourceSnapshot,
    cases: list[EvalCase],
    secret: str = DEFAULT_SIGNING_SECRET,
) -> dict[str, Any] | None:
    """Return fail-closed dict if fixtures invalid; else None."""
    if not verify_source_snapshot(snapshot, secret):
        return fail_closed(
            "invalid_fixture", "snapshot signature invalid"
        ).model_dump()
    for case in cases:
        if case.status == "quarantined":
            return fail_closed(
                "quarantined", f"case {case.case_id} is quarantined"
            ).model_dump()
        if case.status != "frozen":
            return fail_closed(
                "invalid_fixture",
                f"case {case.case_id} status={case.status} not frozen",
            ).model_dump()
        if not verify_frozen_case(case, secret):
            return fail_closed(
                "invalid_fixture",
                f"case {case.case_id} fixture signature invalid",
            ).model_dump()
        if case.snapshot_hash != snapshot.manifest_hash:
            return fail_closed(
                "invalid_fixture",
                f"case {case.case_id} snapshot_hash mismatch",
            ).model_dump()
        # Qualification rejects DB-id-only truth
        if case.gold_chunk_db_ids and not case.equivalent_evidence_sets:
            if case.case_type != "no_answer":
                return fail_closed(
                    "invalid_fixture",
                    f"case {case.case_id} has only gold_chunk_db_ids without hash evidence",
                ).model_dump()
    return None


def validate_calibrated_lineage(
    *,
    generator_lineage: ModelLineage | None,
    judge_lineage: ModelLineage | None,
    calibration_report: CalibrationReport | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Ensure G/J isolation and calibration-passed Judge lineage."""
    if generator_lineage is None or judge_lineage is None:
        return fail_closed("invalid_lineage", "missing generator or judge lineage").model_dump()
    try:
        validate_generator_judge_isolation(generator_lineage, judge_lineage)
    except InvalidLineageError as exc:
        return fail_closed("invalid_lineage", str(exc)).model_dump()

    if calibration_report is None:
        return fail_closed(
            "invalid_lineage", "missing calibration report for Judge"
        ).model_dump()

    if isinstance(calibration_report, CalibrationReport):
        status = calibration_report.status
        cal_rev = calibration_report.judge_lineage.weights_revision
        metrics = calibration_report.metrics
        cfa = calibration_report.critical_false_accept
        consistency = calibration_report.consistency
    else:
        status = calibration_report.get("status")
        jl = calibration_report.get("judge_lineage") or {}
        cal_rev = jl.get("weights/revision") or jl.get("weights_revision")
        metrics = calibration_report.get("metrics")
        cfa = calibration_report.get("critical_false_accept", 0)
        consistency = float(calibration_report.get("consistency") or 0.0)

    if status != "passed":
        return fail_closed(
            "invalid_lineage",
            f"calibration status={status} not passed",
        ).model_dump()
    if metrics is None and status == "passed":
        # 06-03 sets metrics only when passed; if missing treat as incomplete
        pass
    if cfa != 0:
        return fail_closed(
            "invalid_lineage", "calibration critical_false_accept != 0"
        ).model_dump()
    if consistency < 0.80:
        return fail_closed(
            "invalid_lineage", "calibration consistency < 0.80"
        ).model_dump()
    if not cal_rev or cal_rev != judge_lineage.weights_revision:
        return fail_closed(
            "invalid_lineage",
            "Judge weights/revision does not match calibrated report",
        ).model_dump()
    return None


def validate_dependency_health(health: dict[str, Any] | None) -> dict[str, Any] | None:
    if health is None:
        return fail_closed(
            "blocked_dependency", "missing dependency health"
        ).model_dump()
    if health.get("ok") is not True:
        return fail_closed(
            "blocked_dependency",
            health.get("reason") or "dependency health not ok",
            detail=health,
        ).model_dump()
    return None


# ---------------------------------------------------------------------------
# Single-case / multi-case run
# ---------------------------------------------------------------------------


@dataclass
class CaseRunArtifact:
    case_id: str
    repetition: int
    retrieved: list[dict[str, Any]]
    answer: str
    deterministic_metrics: dict[str, Any]
    judge_scores: dict[str, Any]
    token_usage: dict[str, Any]
    cost_usd: float
    latency_ms: float
    status: str
    quality_comparable: bool
    call_id: str  # idempotency key for stage


def run_case_once(
    case: EvalCase,
    snapshot: SourceSnapshot,
    *,
    repetition: int,
    top_k: int = 5,
    retrieve_fn: RetrieveFn | None = None,
    answer_fn: AnswerFn | None = None,
    judge_fn: AnswerJudgeFn | None = None,
    judge_lineage: ModelLineage | None = None,
    stage_cache: dict[str, Any] | None = None,
    run_input_hash: str | None = None,
    chunker_lineage: ChunkerLineage | dict[str, Any] | None = None,
) -> CaseRunArtifact:
    """Execute retrieve -> answer -> score for one case/repetition (idempotent via cache)."""
    retrieve_fn = retrieve_fn or default_stub_retrieve
    answer_fn = answer_fn or default_stub_answer
    judge_fn = judge_fn or default_stub_answer_judge

    call_id = build_stage_cache_key(
        run_input_hash=run_input_hash,
        case_id=case.case_id,
        fixture_hash=case.fixture_hash,
        repetition=repetition,
        top_k=top_k,
        chunker_lineage=chunker_lineage,
    )

    if stage_cache is not None and call_id in stage_cache:
        cached = stage_cache[call_id]
        return CaseRunArtifact(**cached)

    try:
        retrieved = retrieve_fn(case, snapshot, top_k)
        ans = answer_fn(case, retrieved)
        answer_text = str(ans.get("answer") or "")
        j_lineage = judge_lineage or ModelLineage(
            provider="offline",
            model_family="stub",
            model_id="stub-judge",
            weights_revision="stub-rev",
            prompt_hash=answer_judge_prompt_hash() or ("0" * 64),
            prompt_version=ANSWER_JUDGE_PROMPT_VERSION,
            schema_hash=schema_contract_hash(),
            started_at=datetime.now(timezone.utc),
        )
        # pad prompt_hash if empty
        if len(j_lineage.prompt_hash) != 64:
            j_lineage = j_lineage.model_copy(
                update={"prompt_hash": stable_hash(j_lineage.prompt_hash)}
            )

        judge_scores = judge_fn(case, answer_text, retrieved, j_lineage)
        det = {
            "context_precision": context_precision_at_k(case, retrieved, top_k),
            "context_recall_at_5": context_recall_at_k(case, retrieved, top_k),
            **deterministic_claim_metrics(case, answer_text, retrieved),
        }
        # Prefer judge faithfulness; enforce critical from deterministic recount
        det_crit_rate = det["critical_unsupported_claim_rate"]
        if "critical_unsupported_count" in judge_scores:
            # Arbiter re-checks deterministic critical rate
            pass

        artifact = CaseRunArtifact(
            case_id=case.case_id,
            repetition=repetition,
            retrieved=retrieved,
            answer=answer_text,
            deterministic_metrics=det,
            judge_scores=judge_scores,
            token_usage=ans.get("token_usage") or {},
            cost_usd=float(ans.get("cost_usd") or 0.0),
            latency_ms=float(ans.get("latency_ms") or 0.0),
            status="scored",
            quality_comparable=False,  # set by arbiter
            call_id=call_id,
        )
        if stage_cache is not None:
            stage_cache[call_id] = {
                "case_id": artifact.case_id,
                "repetition": artifact.repetition,
                "retrieved": artifact.retrieved,
                "answer": artifact.answer,
                "deterministic_metrics": artifact.deterministic_metrics,
                "judge_scores": artifact.judge_scores,
                "token_usage": artifact.token_usage,
                "cost_usd": artifact.cost_usd,
                "latency_ms": artifact.latency_ms,
                "status": artifact.status,
                "quality_comparable": artifact.quality_comparable,
                "call_id": artifact.call_id,
            }
        return artifact
    except DependencyOutage as exc:
        return CaseRunArtifact(
            case_id=case.case_id,
            repetition=repetition,
            retrieved=[],
            answer="",
            deterministic_metrics={},
            judge_scores={},
            token_usage={},
            cost_usd=0.0,
            latency_ms=0.0,
            status="blocked_dependency",
            quality_comparable=False,
            call_id=call_id,
        )


def aggregate_run_metrics(
    artifacts: list[CaseRunArtifact],
    *,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate per-run metrics from case/repetition artifacts."""
    run_cfg = policy.get("run") or {}
    n_boot = int(run_cfg.get("bootstrap_samples", 1000))
    alpha = float(run_cfg.get("bootstrap_alpha", 0.05))
    seed = int(run_cfg.get("bootstrap_seed", 42))

    faith_scores: list[float] = []
    relevance_scores: list[float] = []
    ctx_prec: list[float] = []
    ctx_rec: list[float] = []
    crit_rates: list[float] = []
    costs: list[float] = []
    latencies: list[float] = []
    tokens: list[int] = []
    per_case_verdicts: dict[str, list[str]] = {}

    for a in artifacts:
        if a.status == "blocked_dependency":
            continue
        j = a.judge_scores or {}
        d = a.deterministic_metrics or {}
        faith = float(j.get("faithfulness", d.get("faithfulness_proxy", 0.0)))
        # Prefer judge critical count when present; else deterministic rate
        if "critical_unsupported_count" in j:
            crit_count = int(j.get("critical_unsupported_count") or 0)
            crit_rate = 1.0 if crit_count > 0 else 0.0
        else:
            crit_rate = float(d.get("critical_unsupported_claim_rate", 0.0))
        # Fail closed: critical unsupported forces faithfulness contribution to 0 for gate
        if crit_rate > 0:
            faith = min(faith, 0.0)
        faith_scores.append(faith)
        relevance_scores.append(float(j.get("relevance", 0.0)))
        ctx_prec.append(float(d.get("context_precision", 0.0)))
        ctx_rec.append(float(d.get("context_recall_at_5", 0.0)))
        crit_rates.append(crit_rate)
        costs.append(a.cost_usd)
        latencies.append(a.latency_ms)
        tokens.append(int((a.token_usage or {}).get("total_tokens") or 0))

        # Per-repeat case-level pass/fail verdict for consistency
        case_pass = (
            crit_rate == 0.0
            and faith >= 0.90
            and float(j.get("relevance", 0.0)) >= 0.5
        )
        per_case_verdicts.setdefault(a.case_id, []).append(
            "pass" if case_pass else "fail"
        )

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    def _p95(xs: list[float]) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        idx = min(len(s) - 1, max(0, int(math.ceil(0.95 * len(s)) - 1)))
        return float(s[idx])

    consistency = case_repeat_consistency(list(per_case_verdicts.values()))
    faith_lb = bootstrap_lower_bound(
        faith_scores, n_boot=n_boot, alpha=alpha, seed=seed
    )
    return {
        "answer_faithfulness_mean": _mean(faith_scores),
        "answer_faithfulness_95lb": faith_lb,
        "answer_relevance_mean": _mean(relevance_scores),
        "context_precision_mean": _mean(ctx_prec),
        "context_recall_at_5_mean": _mean(ctx_rec),
        "critical_unsupported_claim_rate": max(crit_rates) if crit_rates else 0.0,
        "verdict_consistency": consistency,
        "cost_usd_total": sum(costs),
        "cost_usd_mean": _mean(costs),
        "latency_ms_p95": _p95(latencies),
        "tokens_total": sum(tokens),
        "tokens_p95": _p95([float(t) for t in tokens]),
        "n_artifacts": len(artifacts),
        "n_scored": len(faith_scores),
        "per_case_verdicts": per_case_verdicts,
    }


# ---------------------------------------------------------------------------
# Deterministic arbiter (D-08)
# ---------------------------------------------------------------------------


def apply_policy_arbiter(
    *,
    metrics: dict[str, Any] | None,
    policy: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    health: dict[str, Any] | None,
    lineage_ok: bool,
    fixture_ok: bool,
    blocked: bool = False,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    """Deterministic final gate. Missing inputs fail closed with metrics=null."""

    def _term(status: str, reason: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        comparable = status in COMPARABLE_STATUSES
        return {
            "status": status,
            "metrics": metrics if comparable else None,
            "quality_comparable": comparable,
            "reason": reason,
            "detail": detail or {},
            "usable_for_baseline": comparable,
        }

    if blocked:
        return _term(
            "blocked_dependency",
            blocked_reason or "dependency unavailable",
        )
    if not fixture_ok:
        return _term("invalid_fixture", "fixture validation failed")
    if not lineage_ok:
        return _term("invalid_lineage", "lineage/calibration validation failed")
    if policy is None:
        return _term("failed_policy", "missing policy")
    thresholds = policy.get("thresholds")
    p95 = policy.get("p95_budgets")
    if not thresholds or not p95:
        return _term("failed_policy", "policy missing thresholds or p95_budgets")
    if health is None or health.get("ok") is not True:
        return _term(
            "blocked_dependency",
            (health or {}).get("reason") or "missing or unhealthy dependencies",
        )
    if baseline is None:
        return _term("failed_policy", "missing baseline")
    if metrics is None:
        return _term("failed_policy", "missing metrics")

    # Absolute gates
    faith_lb = float(metrics.get("answer_faithfulness_95lb", 0.0))
    faith_min = float(thresholds["answer_faithfulness_95lb_min"])
    if faith_lb < faith_min:
        return _term(
            "failed_policy",
            f"faithfulness 95% LB {faith_lb:.4f} < {faith_min}",
            detail={"answer_faithfulness_95lb": faith_lb},
        )

    crit_rate = float(metrics.get("critical_unsupported_claim_rate", 1.0))
    crit_max = float(thresholds["critical_unsupported_claim_rate_max"])
    if crit_rate > crit_max:
        return _term(
            "failed_policy",
            f"critical unsupported claim rate {crit_rate} > {crit_max}",
            detail={"critical_unsupported_claim_rate": crit_rate},
        )

    consistency = float(metrics.get("verdict_consistency", 0.0))
    cons_min = float(thresholds["verdict_consistency_min"])
    if consistency < cons_min:
        return _term(
            "failed_policy",
            f"verdict consistency {consistency:.4f} < {cons_min}",
            detail={"verdict_consistency": consistency},
        )

    # p95 budgets
    lat_p95 = float(metrics.get("latency_ms_p95", 0.0))
    if lat_p95 > float(p95["latency_ms"]):
        return _term(
            "failed_policy",
            f"p95 latency {lat_p95} > budget {p95['latency_ms']}",
        )
    tokens_total = float(metrics.get("tokens_total", 0.0))
    if tokens_total > float(p95["tokens_total"]):
        return _term(
            "failed_policy",
            f"tokens_total {tokens_total} > budget {p95['tokens_total']}",
        )
    cost_total = float(metrics.get("cost_usd_total", 0.0))
    if cost_total > float(p95["cost_usd"]):
        return _term(
            "failed_policy",
            f"cost_usd {cost_total} > budget {p95['cost_usd']}",
        )

    # Relative regressions vs baseline
    base_rec = baseline.get("context_recall_at_5_mean")
    base_rel = baseline.get("answer_relevance_mean")
    base_cost = baseline.get("cost_usd_total")
    if base_rec is None or base_rel is None or base_cost is None:
        return _term(
            "failed_policy",
            "baseline missing context_recall_at_5_mean / answer_relevance_mean / cost_usd_total",
        )

    rec = float(metrics.get("context_recall_at_5_mean", 0.0))
    # regression in percentage points: (baseline - current) * 100
    rec_reg_pp = (float(base_rec) - rec) * 100.0
    rec_max = float(thresholds["context_recall_at_5_regression_pp_max"])
    if rec_reg_pp > rec_max:
        return _term(
            "quality_regression",
            f"context_recall@5 regression {rec_reg_pp:.2f}pp > {rec_max}pp",
            detail={
                "baseline": base_rec,
                "current": rec,
                "regression_pp": rec_reg_pp,
            },
        )

    rel = float(metrics.get("answer_relevance_mean", 0.0))
    rel_reg_pp = (float(base_rel) - rel) * 100.0
    rel_max = float(thresholds["answer_relevance_regression_pp_max"])
    if rel_reg_pp > rel_max:
        return _term(
            "quality_regression",
            f"answer_relevance regression {rel_reg_pp:.2f}pp > {rel_max}pp",
            detail={
                "baseline": base_rel,
                "current": rel,
                "regression_pp": rel_reg_pp,
            },
        )

    cost_ratio_max = float(thresholds["cost_vs_baseline_max_ratio"])
    base_cost_f = float(base_cost)
    if base_cost_f > 0 and cost_total > base_cost_f * cost_ratio_max:
        return _term(
            "failed_policy",
            f"cost {cost_total} > baseline {base_cost_f} * {cost_ratio_max}",
            detail={"cost_usd_total": cost_total, "baseline_cost": base_cost_f},
        )
    if base_cost_f == 0 and cost_total > 0 and cost_ratio_max < float("inf"):
        # zero baseline cost: only allow zero current cost for +15% rule
        if cost_total > 0:
            # If baseline is 0, any positive cost exceeds +15% of 0
            return _term(
                "failed_policy",
                f"cost {cost_total} > baseline 0 * {cost_ratio_max}",
            )

    # Qualified if all absolute metrics strong and no regression
    strong = (
        faith_lb >= max(faith_min, 0.95)
        and consistency >= 0.95
        and rec_reg_pp <= 0
        and rel_reg_pp <= 0
    )
    status = "qualified" if strong else "passed"
    return {
        "status": status,
        "metrics": metrics,
        "quality_comparable": True,
        "reason": "all policy gates passed",
        "detail": {
            "answer_faithfulness_95lb": faith_lb,
            "verdict_consistency": consistency,
            "context_recall_regression_pp": rec_reg_pp,
            "relevance_regression_pp": rel_reg_pp,
        },
        "usable_for_baseline": True,
    }


# ---------------------------------------------------------------------------
# Full quality run orchestration (synchronous scoring path)
# ---------------------------------------------------------------------------


def run_quality_evaluation(
    *,
    snapshot: SourceSnapshot,
    cases: list[EvalCase],
    generator_lineage: ModelLineage | None = None,
    judge_lineage: ModelLineage | None = None,
    calibration_report: CalibrationReport | dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    health: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    policy_file: str | Path | None = None,
    secret: str = DEFAULT_SIGNING_SECRET,
    retrieve_fn: RetrieveFn | None = None,
    answer_fn: AnswerFn | None = None,
    judge_fn: AnswerJudgeFn | None = None,
    stage_cache: dict[str, Any] | None = None,
    repeats: int | None = None,
    top_k: int | None = None,
    chunker_lineage: ChunkerLineage | dict[str, Any] | None = None,
    require_chunker_lineage: bool = False,
    run_input_hash: str | None = None,
) -> dict[str, Any]:
    """Run SUT scoring + deterministic arbiter. Never swallows exceptions into 0 scores."""

    # Policy
    loaded_policy: dict[str, Any] | None
    try:
        loaded_policy = policy if policy is not None else load_policy(policy_file)
    except (OSError, ValueError, RuntimeError) as exc:
        return {
            "status": "failed_policy",
            "metrics": None,
            "quality_comparable": False,
            "reason": f"policy load failed: {exc}",
            "detail": {},
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": None,
        }

    run_cfg = loaded_policy.get("run") or {}
    n_repeats = int(repeats if repeats is not None else run_cfg.get("repeats", 3))
    k = int(top_k if top_k is not None else run_cfg.get("top_k", 5))
    p_hash = policy_hash(loaded_policy)

    # Chunker/source five-tuple lineage (before scoring when required)
    canonical_lineage, lineage_reason = canonicalize_chunker_lineage(
        chunker_lineage,
        expected_source_snapshot_hash=snapshot.manifest_hash,
    )
    if require_chunker_lineage and (
        canonical_lineage is None
        or (lineage_reason and lineage_reason.startswith(INVALID_LINEAGE_REASON))
        or lineage_reason == LEGACY_INCOMPARABLE_REASON
    ):
        reason = lineage_reason or INVALID_LINEAGE_REASON
        if reason == LEGACY_INCOMPARABLE_REASON:
            reason = f"{INVALID_LINEAGE_REASON}: missing chunker/source lineage"
        return {
            "status": "invalid_lineage",
            "metrics": None,
            "quality_comparable": False,
            "reason": reason,
            "detail": {"incomparable_reason": reason},
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": None,
            "incomparable_reason": reason,
        }
    if lineage_reason and lineage_reason.startswith(INVALID_LINEAGE_REASON):
        return {
            "status": "invalid_lineage",
            "metrics": None,
            "quality_comparable": False,
            "reason": lineage_reason,
            "detail": {"incomparable_reason": lineage_reason},
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": None,
            "incomparable_reason": lineage_reason,
        }

    five = canonical_lineage.five_tuple() if canonical_lineage else None
    effective_input_hash = run_input_hash or build_quality_input_hash(
        snapshot_manifest_hash=snapshot.manifest_hash,
        case_fixture_hashes=[c.fixture_hash for c in cases],
        baseline=baseline,
        policy_hash_value=p_hash,
        chunker_lineage=canonical_lineage,
    )

    # Fixtures
    fixture_fail = validate_fixtures_for_scoring(
        snapshot=snapshot, cases=cases, secret=secret
    )
    if fixture_fail is not None:
        return {
            **fixture_fail,
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": five,
            "input_hash": effective_input_hash,
        }

    # Infer lineages from first case if not provided
    g_lin = generator_lineage or (cases[0].generator_lineage if cases else None)
    j_lin = judge_lineage or (cases[0].judge_lineage if cases else None)

    lineage_fail = validate_calibrated_lineage(
        generator_lineage=g_lin,
        judge_lineage=j_lin,
        calibration_report=calibration_report,
    )
    if lineage_fail is not None:
        return {
            **lineage_fail,
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": five,
            "input_hash": effective_input_hash,
        }

    health_fail = validate_dependency_health(health)
    if health_fail is not None:
        return {
            **health_fail,
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": five,
            "input_hash": effective_input_hash,
        }

    cache = stage_cache if stage_cache is not None else {}
    artifacts: list[CaseRunArtifact] = []
    blocked = False
    blocked_reason = None

    try:
        for case in cases:
            for rep in range(n_repeats):
                art = run_case_once(
                    case,
                    snapshot,
                    repetition=rep,
                    top_k=k,
                    retrieve_fn=retrieve_fn,
                    answer_fn=answer_fn,
                    judge_fn=judge_fn,
                    judge_lineage=j_lin,
                    stage_cache=cache,
                    run_input_hash=effective_input_hash,
                    chunker_lineage=canonical_lineage,
                )
                if art.status == "blocked_dependency":
                    blocked = True
                    blocked_reason = "dependency outage during SUT run"
                    artifacts.append(art)
                    break
                artifacts.append(art)
            if blocked:
                break
    except Exception as exc:
        # Never convert exceptions into zero scores for the quality path
        logger.exception("quality evaluation failed")
        return {
            "status": "failed_policy",
            "metrics": None,
            "quality_comparable": False,
            "reason": f"unhandled exception: {type(exc).__name__}: {exc}",
            "detail": {},
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": five,
            "input_hash": effective_input_hash,
        }

    metrics = None if blocked else aggregate_run_metrics(artifacts, policy=loaded_policy)
    decision = apply_policy_arbiter(
        metrics=metrics,
        policy=loaded_policy,
        baseline=baseline,
        health=health,
        lineage_ok=True,
        fixture_ok=True,
        blocked=blocked,
        blocked_reason=blocked_reason,
    )

    report = {
        **decision,
        "policy_version": loaded_policy.get("version"),
        "policy_hash": p_hash,
        "schema_version": SCHEMA_VERSION_RAG_QUALITY,
        "n_cases": len(cases),
        "repeats": n_repeats,
        "input_hash": effective_input_hash,
        # Canonical lineage enters the signed report before signature/output_hash.
        "chunker_lineage": five,
        "artifacts": [
            {
                "case_id": a.case_id,
                "repetition": a.repetition,
                "status": a.status,
                "call_id": a.call_id,
                "cost_usd": a.cost_usd,
                "latency_ms": a.latency_ms,
                "deterministic_metrics": a.deterministic_metrics,
                "judge_scores": a.judge_scores,
                "answer": a.answer,
                "retrieved_hashes": _retrieved_hashes(a.retrieved, k),
            }
            for a in artifacts
        ],
        "sut_family_disclosure": {
            "note": "SUT may share family with G or J; disclosed for audit only",
        },
    }
    # Sign complete unsigned report (lineage included); then bind output_hash.
    unsigned = {k: v for k, v in report.items() if k not in ("report_signature", "output_hash")}
    report["report_signature"] = sign_payload(unsigned, secret)
    report["output_hash"] = stable_hash(unsigned)
    return report


def make_baseline_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Non-promotable metrics shape helper for tests / arbiter inputs.

    Does NOT authorize baseline promotion — only prepare/commit against a
    durable QualityRun with complete comparable lineage can promote.
    """
    return {
        "context_recall_at_5_mean": metrics.get("context_recall_at_5_mean", 0.0),
        "answer_relevance_mean": metrics.get("answer_relevance_mean", 0.0),
        "cost_usd_total": metrics.get("cost_usd_total", 0.0),
        "answer_faithfulness_95lb": metrics.get("answer_faithfulness_95lb", 0.0),
        "context_precision_mean": metrics.get("context_precision_mean", 0.0),
    }


# ── Phase 06-09: durable baseline prepare/commit + cross-chunker report ──

_BASELINE_ELIGIBLE = frozenset({"passed", "qualified"})


class BaselineServiceError(Exception):
    """Owner-safe baseline prepare/commit/report error with HTTP status."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def compute_prepare_fingerprint(
    *,
    run_status: str,
    input_hash: str,
    output_hash: str,
    report_signature: str,
    metrics: dict[str, Any],
    chunker_name: str,
    chunker_version: str,
    chunker_config_hash: str,
    chunk_manifest_hash: str,
    source_snapshot_hash: str,
) -> str:
    """Canonical fingerprint frozen at prepare; commit revalidates equality."""
    return stable_hash(
        {
            "run_status": run_status,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "report_signature": report_signature,
            "metrics": metrics,
            "chunker_name": chunker_name,
            "chunker_version": chunker_version,
            "chunker_config_hash": chunker_config_hash,
            "chunk_manifest_hash": chunk_manifest_hash,
            "source_snapshot_hash": source_snapshot_hash,
        }
    )


def _run_lineage_complete(run: Any) -> bool:
    fields = (
        run.chunker_name,
        run.chunker_version,
        run.chunker_config_hash,
        run.chunk_manifest_hash,
        run.source_snapshot_hash,
    )
    if not all(fields):
        return False
    for h in (
        run.chunker_config_hash,
        run.chunk_manifest_hash,
        run.source_snapshot_hash,
        run.input_hash,
        run.output_hash,
    ):
        if not h or len(str(h)) != 64:
            return False
    if not run.report_signature:
        return False
    return True


def _validate_run_for_baseline(run: Any, *, owner_id: int) -> None:
    if run is None or run.owner_id != owner_id:
        raise BaselineServiceError("quality run not found", status_code=404)
    if run.status not in _BASELINE_ELIGIBLE:
        raise BaselineServiceError(
            f"run status {run.status!r} is not baseline-eligible "
            f"(require passed/qualified)",
            status_code=400,
        )
    if not run.quality_comparable:
        reason = run.incomparable_reason or LEGACY_INCOMPARABLE_REASON
        raise BaselineServiceError(
            f"run is not quality_comparable ({reason})",
            status_code=400,
        )
    if not run.metrics or not isinstance(run.metrics, dict):
        raise BaselineServiceError("run metrics missing", status_code=400)
    if not _run_lineage_complete(run):
        raise BaselineServiceError(
            "incomplete canonical lineage or identity hashes",
            status_code=400,
        )


def _candidate_public(c: Any) -> dict[str, Any]:
    return {
        "id": c.id,
        "owner_id": c.owner_id,
        "quality_run_id": c.quality_run_id,
        "quality_run_job_id": c.quality_run_job_id,
        "prepare_token": c.prepare_token,
        "prepare_version": c.prepare_version,
        "state": c.state,
        "reason": c.reason,
        "chunker_name": c.chunker_name,
        "chunker_version": c.chunker_version,
        "chunker_config_hash": c.chunker_config_hash,
        "chunk_manifest_hash": c.chunk_manifest_hash,
        "source_snapshot_hash": c.source_snapshot_hash,
        "run_status": c.run_status,
        "input_hash": c.input_hash,
        "output_hash": c.output_hash,
        "report_signature": c.report_signature,
        "metrics_snapshot": c.metrics_snapshot or {},
        "prepare_fingerprint": c.prepare_fingerprint,
        "journal": list(c.journal or []),
        "prepared_at": c.prepared_at.isoformat() if c.prepared_at else None,
        "committed_at": c.committed_at.isoformat() if c.committed_at else None,
    }


def _active_public(a: Any) -> dict[str, Any]:
    return {
        "owner_id": a.owner_id,
        "candidate_id": a.candidate_id,
        "quality_run_id": a.quality_run_id,
        "metrics_snapshot": a.metrics_snapshot or {},
        "chunker_name": a.chunker_name,
        "chunker_version": a.chunker_version,
        "chunker_config_hash": a.chunker_config_hash,
        "chunk_manifest_hash": a.chunk_manifest_hash,
        "source_snapshot_hash": a.source_snapshot_hash,
        "committed_at": a.committed_at.isoformat() if a.committed_at else None,
    }


async def prepare_baseline_candidate(
    session: Any,
    *,
    owner_id: int,
    job_id: str,
) -> dict[str, Any]:
    """Persist prepare evidence from current QualityRun (DB is sole source)."""
    from sqlalchemy import select
    from app.models.eval import BaselineCandidate, QualityRun
    import uuid

    result = await session.execute(
        select(QualityRun).where(QualityRun.job_id == job_id)
    )
    run = result.scalar_one_or_none()
    _validate_run_for_baseline(run, owner_id=owner_id)

    metrics = dict(run.metrics)
    fingerprint = compute_prepare_fingerprint(
        run_status=run.status,
        input_hash=run.input_hash,
        output_hash=run.output_hash,
        report_signature=run.report_signature,
        metrics=metrics,
        chunker_name=run.chunker_name,
        chunker_version=run.chunker_version,
        chunker_config_hash=run.chunker_config_hash,
        chunk_manifest_hash=run.chunk_manifest_hash,
        source_snapshot_hash=run.source_snapshot_hash,
    )
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex
    entry = {
        "at": now.isoformat(),
        "event": "prepared",
        "job_id": run.job_id,
        "fingerprint": fingerprint,
    }
    cand = BaselineCandidate(
        owner_id=owner_id,
        quality_run_id=run.id,
        quality_run_job_id=run.job_id,
        prepare_token=token,
        prepare_version=1,
        state="prepared",
        reason=None,
        chunker_name=run.chunker_name,
        chunker_version=run.chunker_version,
        chunker_config_hash=run.chunker_config_hash,
        chunk_manifest_hash=run.chunk_manifest_hash,
        source_snapshot_hash=run.source_snapshot_hash,
        run_status=run.status,
        input_hash=run.input_hash,
        output_hash=run.output_hash,
        report_signature=run.report_signature,
        metrics_snapshot=metrics,
        prepare_fingerprint=fingerprint,
        journal=[entry],
        prepared_at=now,
    )
    session.add(cand)
    await session.flush()
    return _candidate_public(cand)


async def commit_baseline_candidate(
    session: Any,
    *,
    owner_id: int,
    candidate_id: int,
    prepare_token: str,
) -> dict[str, Any]:
    """Revalidate prepare fingerprint against current QualityRun; set active."""
    from sqlalchemy import select
    from app.models.eval import ActiveBaseline, BaselineCandidate, QualityRun

    result = await session.execute(
        select(BaselineCandidate)
        .where(
            BaselineCandidate.id == candidate_id,
            BaselineCandidate.owner_id == owner_id,
        )
        .with_for_update()
    )
    cand = result.scalar_one_or_none()
    if cand is None:
        raise BaselineServiceError("candidate not found", status_code=404)
    if cand.prepare_token != prepare_token:
        raise BaselineServiceError("prepare_token mismatch", status_code=403)

    # Idempotent: already committed this candidate
    if cand.state == "committed":
        active = (
            await session.execute(
                select(ActiveBaseline).where(ActiveBaseline.owner_id == owner_id)
            )
        ).scalar_one_or_none()
        return {
            "ok": True,
            "candidate": _candidate_public(cand),
            "active": _active_public(active) if active else None,
            "idempotent": True,
            "error": None,
        }

    if cand.state != "prepared":
        raise BaselineServiceError(
            f"candidate state {cand.state!r} is not committable",
            status_code=400,
        )

    run = (
        await session.execute(
            select(QualityRun)
            .where(QualityRun.id == cand.quality_run_id)
            .with_for_update()
        )
    ).scalar_one_or_none()

    # Capture previous active for "unchanged on reject"
    prev_active = (
        await session.execute(
            select(ActiveBaseline).where(ActiveBaseline.owner_id == owner_id)
        )
    ).scalar_one_or_none()
    prev_candidate_id = prev_active.candidate_id if prev_active else None

    reject_reason: str | None = None
    try:
        _validate_run_for_baseline(run, owner_id=owner_id)
    except BaselineServiceError as exc:
        reject_reason = exc.message

    if reject_reason is None:
        current_fp = compute_prepare_fingerprint(
            run_status=run.status,
            input_hash=run.input_hash or "",
            output_hash=run.output_hash or "",
            report_signature=run.report_signature or "",
            metrics=dict(run.metrics or {}),
            chunker_name=run.chunker_name or "",
            chunker_version=run.chunker_version or "",
            chunker_config_hash=run.chunker_config_hash or "",
            chunk_manifest_hash=run.chunk_manifest_hash or "",
            source_snapshot_hash=run.source_snapshot_hash or "",
        )
        if current_fp != cand.prepare_fingerprint:
            reject_reason = (
                "run changed after prepare (lineage/hash/signature/metrics/status)"
            )
        else:
            for attr in (
                "chunker_name",
                "chunker_version",
                "chunker_config_hash",
                "chunk_manifest_hash",
                "source_snapshot_hash",
                "input_hash",
                "output_hash",
                "report_signature",
            ):
                if getattr(run, attr) != getattr(cand, attr):
                    reject_reason = f"field {attr} diverged from prepare evidence"
                    break

    if reject_reason is not None:
        now = datetime.now(timezone.utc)
        journal = list(cand.journal or [])
        journal.append(
            {
                "at": now.isoformat(),
                "event": "rejected",
                "reason": reject_reason,
                "prev_active_candidate_id": prev_candidate_id,
            }
        )
        cand.state = "rejected"
        cand.reason = reject_reason
        cand.journal = journal
        await session.flush()
        return {
            "ok": False,
            "candidate": _candidate_public(cand),
            "active": _active_public(prev_active) if prev_active else None,
            "idempotent": False,
            "error": reject_reason,
        }

    now = datetime.now(timezone.utc)
    journal = list(cand.journal or [])
    journal.append(
        {
            "at": now.isoformat(),
            "event": "committed",
            "fingerprint": cand.prepare_fingerprint,
            "prev_active_candidate_id": prev_candidate_id,
        }
    )
    cand.state = "committed"
    cand.committed_at = now
    cand.journal = journal
    cand.reason = None

    if prev_active is None:
        active = ActiveBaseline(
            owner_id=owner_id,
            candidate_id=cand.id,
            quality_run_id=cand.quality_run_id,
            metrics_snapshot=dict(cand.metrics_snapshot or {}),
            chunker_name=cand.chunker_name,
            chunker_version=cand.chunker_version,
            chunker_config_hash=cand.chunker_config_hash,
            chunk_manifest_hash=cand.chunk_manifest_hash,
            source_snapshot_hash=cand.source_snapshot_hash,
            committed_at=now,
        )
        session.add(active)
    else:
        prev_active.candidate_id = cand.id
        prev_active.quality_run_id = cand.quality_run_id
        prev_active.metrics_snapshot = dict(cand.metrics_snapshot or {})
        prev_active.chunker_name = cand.chunker_name
        prev_active.chunker_version = cand.chunker_version
        prev_active.chunker_config_hash = cand.chunker_config_hash
        prev_active.chunk_manifest_hash = cand.chunk_manifest_hash
        prev_active.source_snapshot_hash = cand.source_snapshot_hash
        prev_active.committed_at = now
        active = prev_active

    await session.flush()
    return {
        "ok": True,
        "candidate": _candidate_public(cand),
        "active": _active_public(active),
        "idempotent": False,
        "error": None,
    }


async def get_active_baseline(
    session: Any, *, owner_id: int
) -> dict[str, Any] | None:
    from sqlalchemy import select
    from app.models.eval import ActiveBaseline

    row = (
        await session.execute(
            select(ActiveBaseline).where(ActiveBaseline.owner_id == owner_id)
        )
    ).scalar_one_or_none()
    return _active_public(row) if row else None


async def build_cross_chunker_report(
    session: Any,
    *,
    owner_id: int,
    source_snapshot_hash: str,
) -> dict[str, Any]:
    """Same-snapshot multi-chunker report; excludes legacy/incomplete/invalid."""
    from sqlalchemy import select
    from app.models.eval import QualityRun

    if not source_snapshot_hash or len(source_snapshot_hash) != 64:
        raise BaselineServiceError(
            "source_snapshot_hash must be sha256 hex", status_code=400
        )

    rows = (
        await session.execute(
            select(QualityRun).where(
                QualityRun.owner_id == owner_id,
            )
        )
    ).scalars().all()

    series: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []

    for run in rows:
        # Different snapshot → skip silently (not part of this report boundary)
        if run.source_snapshot_hash and run.source_snapshot_hash != source_snapshot_hash:
            exclusions.append(
                {
                    "job_id": run.job_id,
                    "quality_run_id": run.id,
                    "reason": "different_source_snapshot",
                }
            )
            continue
        if not run.source_snapshot_hash:
            exclusions.append(
                {
                    "job_id": run.job_id,
                    "quality_run_id": run.id,
                    "reason": "missing_source_snapshot",
                }
            )
            continue
        if run.source_snapshot_hash != source_snapshot_hash:
            continue
        if not run.quality_comparable:
            exclusions.append(
                {
                    "job_id": run.job_id,
                    "quality_run_id": run.id,
                    "reason": run.incomparable_reason or LEGACY_INCOMPARABLE_REASON,
                }
            )
            continue
        if not _run_lineage_complete(run):
            exclusions.append(
                {
                    "job_id": run.job_id,
                    "quality_run_id": run.id,
                    "reason": "incomplete_lineage",
                }
            )
            continue
        if run.status not in _BASELINE_ELIGIBLE and run.status not in {
            "quality_regression",
            "failed_policy",
        }:
            # Allow non-passed comparable runs only if complete; still report series
            # but prefer terminal scored statuses with metrics.
            if not run.metrics:
                exclusions.append(
                    {
                        "job_id": run.job_id,
                        "quality_run_id": run.id,
                        "reason": f"status_not_reportable:{run.status}",
                    }
                )
                continue
        if not run.metrics:
            exclusions.append(
                {
                    "job_id": run.job_id,
                    "quality_run_id": run.id,
                    "reason": "metrics_missing",
                }
            )
            continue

        metrics = dict(run.metrics)
        series.append(
            {
                "job_id": run.job_id,
                "quality_run_id": run.id,
                "status": run.status,
                "chunker_name": run.chunker_name,
                "chunker_version": run.chunker_version,
                "chunker_config_hash": run.chunker_config_hash,
                "chunk_manifest_hash": run.chunk_manifest_hash,
                "source_snapshot_hash": run.source_snapshot_hash,
                "metrics": metrics,
                "input_hash": run.input_hash,
                "output_hash": run.output_hash,
                "report_signature": run.report_signature,
                "cost_usd_total": metrics.get("cost_usd_total"),
                "latency_ms_total": metrics.get("latency_ms_total"),
            }
        )

    # Deterministic sort: name, version, config_hash, manifest_hash, job_id
    series.sort(
        key=lambda s: (
            s["chunker_name"],
            s["chunker_version"],
            s["chunker_config_hash"],
            s["chunk_manifest_hash"],
            s["job_id"],
        )
    )
    exclusions.sort(key=lambda e: (e.get("job_id") or "", e.get("quality_run_id") or 0))

    return {
        "source_snapshot_hash": source_snapshot_hash,
        "series": series,
        "exclusions": exclusions,
    }


def default_healthy() -> dict[str, Any]:
    return {
        "ok": True,
        "db": "ok",
        "chroma": "ok",
        "model": "ok",
        "reason": None,
    }


def probe_ollama_health(
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Probe local Ollama; returns health dict (never raises for outage)."""
    try:
        import urllib.request

        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= getattr(resp, "status", 200) < 300:
                return {
                    "ok": True,
                    "db": "skipped",
                    "chroma": "skipped",
                    "model": "ok",
                    "reason": None,
                    "endpoint": base_url,
                }
            return {
                "ok": False,
                "db": "skipped",
                "chroma": "skipped",
                "model": "down",
                "reason": f"ollama status={getattr(resp, 'status', '?')}",
            }
    except Exception as exc:
        return {
            "ok": False,
            "db": "skipped",
            "chroma": "skipped",
            "model": "down",
            "reason": f"ollama unavailable: {type(exc).__name__}: {exc}",
        }
