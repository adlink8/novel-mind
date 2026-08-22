"""Protocol types for stubbable SUT + Judge (rag_quality package)."""

from __future__ import annotations

from typing import Any, Callable

from app.schemas.eval import EvalCase, ModelLineage, SourceSnapshot

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
