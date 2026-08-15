"""Clue worker shared primitives — runtime contract, errors, cost, hashes.

拆分说明（refactor split）：worker 编排与 judge/persist seam 共享的原语下沉到
本叶模块——``ClueWorkerRuntime`` 注入契约、``ClueModelDeployment`` 部署快照、
worker 异常（``ClueWorkerError`` / ``ClueCancellationRequested`` /
``DependencyPaused``）、``compute_actual_cost_usd`` 实际成本结算、
``CONFIG_HASH`` / ``DECODING_HASH`` 与 ``COST_REASON_UNKNOWN_PRICING``。
本模块不 import 任何 worker 模块，保证无环。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.clues.budget import ClueCallRepository, BudgetPolicy
from app.services.clues.candidates import (
    ClueCandidateRecallService,
    clue_candidate_recall_service,
)
from app.services.clues.evidence import sha256_json, sha256_text
from app.services.clues.gates import ClueGateService, clue_gate_service
from app.services.clues.llm_judge import ClueLLMJudgeService, clue_llm_judge_service

__all__ = [
    "CONFIG_HASH",
    "DECODING_HASH",
    "COST_REASON_UNKNOWN_PRICING",
    "ClueCancellationRequested",
    "ClueModelDeployment",
    "ClueWorkerError",
    "ClueWorkerRuntime",
    "DependencyPaused",
    "compute_actual_cost_usd",
]

CONFIG_HASH = sha256_text("clue-worker.v1")
DECODING_HASH = sha256_json(
    {"temperature": 0.0, "stream": False, "provider_retries": 0, "max_tokens": 1200}
)


COST_REASON_UNKNOWN_PRICING = "unknown_pricing"


def compute_actual_cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    input_price_per_million: Decimal | None,
    output_price_per_million: Decimal | None,
) -> tuple[Decimal, str | None]:
    """Actual settlement cost from usage × deployment price snapshot.

    Mirrors timeline/narrative-memory gateway settlement. When the price
    snapshot is missing, the cost is explicitly 0 with a reason — never a
    silent zero.
    """
    if input_price_per_million is None or output_price_per_million is None:
        return Decimal("0"), COST_REASON_UNKNOWN_PRICING
    cost = (
        Decimal(int(input_tokens)) * input_price_per_million
        + Decimal(int(output_tokens)) * output_price_per_million
    ) / Decimal(1_000_000)
    return cost, None


class ClueWorkerError(RuntimeError):
    pass


class ClueCancellationRequested(RuntimeError):
    pass


class DependencyPaused(RuntimeError):
    pass


@dataclass(frozen=True)
class ClueModelDeployment:
    provider: str
    model_id: str
    revision: str
    input_price_per_million: Decimal | None
    output_price_per_million: Decimal | None

    @property
    def lineage(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "revision": self.revision,
        }


@dataclass
class ClueWorkerRuntime:
    sessions: async_sessionmaker[AsyncSession]
    call_repo: ClueCallRepository
    deployment: ClueModelDeployment
    judge: ClueLLMJudgeService = field(default_factory=lambda: clue_llm_judge_service)
    recall: ClueCandidateRecallService = field(
        default_factory=lambda: clue_candidate_recall_service
    )
    gates: ClueGateService = field(default_factory=lambda: clue_gate_service)
    budget_policy: BudgetPolicy = field(
        default_factory=lambda: BudgetPolicy(
            max_calls=500,
            max_input_tokens=5_000_000,
            max_output_tokens=500_000,
            max_cost_usd=Decimal("25"),
        )
    )
    # Test hook: candidate_id → judgment dict (skips network; still reserves if configured).
    deterministic_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # When True, deterministic outputs count as cache hits (zero provider calls).
    deterministic_as_cache: bool = True
