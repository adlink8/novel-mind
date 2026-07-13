"""Per-boundary and run-level hard budget accounting for adjudication (07-03)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BudgetConfig:
    max_input_tokens_per_call: int = 2000
    max_output_tokens_per_call: int = 180
    max_attempts_per_boundary: int = 2
    max_boundaries_per_run: int = 50
    max_total_input_tokens: int = 40_000
    max_total_output_tokens: int = 4_000
    timeout_seconds: float = 20.0
    max_concurrency: int = 4


@dataclass
class BudgetLedger:
    config: BudgetConfig = field(default_factory=BudgetConfig)
    boundaries_scheduled: int = 0
    boundaries_called: int = 0
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    skipped_budget: list[str] = field(default_factory=list)

    def worst_case_ok(self, pending_count: int) -> bool:
        cfg = self.config
        if pending_count > cfg.max_boundaries_per_run:
            return False
        worst_in = pending_count * cfg.max_attempts_per_boundary * cfg.max_input_tokens_per_call
        worst_out = (
            pending_count * cfg.max_attempts_per_boundary * cfg.max_output_tokens_per_call
        )
        return (
            worst_in <= cfg.max_total_input_tokens
            and worst_out <= cfg.max_total_output_tokens
        )

    def can_call(self, boundary_id: str, estimated_input_tokens: int) -> tuple[bool, str]:
        cfg = self.config
        if self.boundaries_called >= cfg.max_boundaries_per_run:
            self.skipped_budget.append(boundary_id)
            return False, "max_boundaries_per_run"
        if estimated_input_tokens > cfg.max_input_tokens_per_call:
            self.skipped_budget.append(boundary_id)
            return False, "input_tokens_exceed_per_call"
        if self.input_tokens + estimated_input_tokens > cfg.max_total_input_tokens:
            self.skipped_budget.append(boundary_id)
            return False, "run_input_budget_exhausted"
        if (
            self.output_tokens + cfg.max_output_tokens_per_call
            > cfg.max_total_output_tokens
        ):
            self.skipped_budget.append(boundary_id)
            return False, "run_output_budget_exhausted"
        return True, "ok"

    def record_attempt(
        self,
        *,
        boundary_id: str,
        tokens_in: int,
        tokens_out: int,
        first_for_boundary: bool,
    ) -> None:
        self.attempts += 1
        if first_for_boundary:
            self.boundaries_called += 1
        self.input_tokens += max(0, tokens_in)
        self.output_tokens += max(0, tokens_out)

    def estimate_tokens(self, text: str) -> int:
        # Conservative: ~1 token per 2 chars for mixed CJK/EN
        return max(1, (len(text) + 1) // 2)
