"""Phase 10 reader-chat services (lifecycle, context, generation)."""

from app.services.reader_chat.conversations import (
    ContextBuilder,
    ContextGraph,
    DeterministicContextBuilder,
    ProductionContextBuilder,
    conversation_service,
)

__all__ = [
    "ContextBuilder",
    "ContextGraph",
    "DeterministicContextBuilder",
    "ProductionContextBuilder",
    "conversation_service",
]


def __getattr__(name: str):
    # Lazy exports for worker/gateway to keep import side-effects light.
    if name in {
        "DualBudgetGate",
        "BudgetExceeded",
        "UnknownPricing",
        "DualBudgetRepository",
    }:
        from app.services.reader_chat import budget as _budget

        return getattr(_budget, name)
    if name in {
        "ReaderChatGateway",
        "ModelDeployment",
        "StructuredOutputRejected",
    }:
        from app.services.reader_chat import gateway as _gateway

        return getattr(_gateway, name)
    if name in {
        "run_reader_chat_worker",
        "dispatch_reader_chat_job",
        "production_runtime",
    }:
        from app.services.reader_chat import worker as _worker

        return getattr(_worker, name)
    raise AttributeError(name)
