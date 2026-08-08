"""Versioned quality policy loading (rag_quality package)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.rag_fixture import prompts_dir, stable_hash

POLICY_VERSION = "rag-quality-policy.v1"
ANSWER_JUDGE_PROMPT_VERSION = "rag_answer_judge.v1"


def policy_path() -> Path:
    # Package lives one level deeper than the original rag_quality.py module:
    # parents[3] = backend root (evals/rag-quality-policy.v1.yml).
    return Path(__file__).resolve().parents[3] / "evals" / "rag-quality-policy.v1.yml"


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
    return (
        stable_hash(path.read_text(encoding="utf-8"))
        if path.is_file()
        else stable_hash("")
    )
