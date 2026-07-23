"""Adversarial static gates for Phase 15 hierarchical retrieval safety.

PostgreSQL future-metadata / IDOR / citation-tamper suites live in
``tests/integration/narrative_memory/test_retrieval_adversarial_pg.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.config import settings
from app.services.narrative_memory.retrieval_contracts import (
    LeafCitation,
    RouteDecision,
    RouteMode,
    RouteReasonCode,
    StartLevel,
)
from app.services.narrative_memory.routing import (
    ROUTING_POLICY_HASH,
    ROUTING_POLICY_VERSION,
)
from pydantic import ValidationError


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
NM = ROOT / "app" / "services" / "narrative_memory"
PHASE15 = (
    "retrieval_contracts.py",
    "routing.py",
    "candidate_reader.py",
    "descent.py",
    "citations.py",
    "retrieval_manifests.py",
    "experiments.py",
)


def test_experiment_default_off():
    assert settings.narrative_memory_retrieval_experiment_enabled is False


def test_phase15_sources_ban_reader_chat_and_provider_imports():
    forbidden = (
        "app.services.reader_chat",
        "app.models.reader_chat",
        "app.api.reader_chat",
        "litellm",
        "openai",
        "set_active_pointer",
        "promote_timeline",
    )
    for name in PHASE15:
        source = (NM / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for token in forbidden:
                    if token.startswith("app.") or token in {"litellm", "openai"}:
                        assert not node.module.startswith(token), (
                            f"{name}:{node.module}"
                        )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in {"litellm", "openai"}
        for token in ("from app.services.reader_chat", "from app.models.reader_chat"):
            assert token not in source
        assert "def set_active_pointer" not in source
        assert "def promote_timeline" not in source


def test_public_route_and_citation_reject_leaky_fields():
    with pytest.raises(ValidationError):
        RouteDecision.model_validate(
            {
                "mode": "local",
                "start_levels": ["chapter_state"],
                "reason_codes": ["safe_default"],
                "policy_version": ROUTING_POLICY_VERSION,
                "policy_hash": ROUTING_POLICY_HASH,
                "rationale": "leaky free text",
            }
        )
    with pytest.raises(ValidationError):
        LeafCitation.model_validate(
            {
                "chapter_id": 1,
                "chapter_number": 1,
                "evidence_node_id": "e",
                "hierarchy_build_id": "b",
                "source_start": 0,
                "source_end": 1,
                "content_hash": "a" * 64,
                "excerpt": "x",
                "source_snapshot_hash": "a" * 64,
                "similarity_score": 0.99,
            }
        )


def test_route_modes_are_closed_enum():
    assert {m.value for m in RouteMode} == {"local", "arc", "global", "mixed"}
    assert StartLevel.CHAPTER_STATE.value == "chapter_state"
    assert RouteReasonCode.UNAUTHORIZED_GLOBAL.value == "unauthorized_global"
