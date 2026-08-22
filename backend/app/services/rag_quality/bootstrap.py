"""Bootstrap / consistency statistics (rag_quality package)."""

from __future__ import annotations

import math
import random


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
