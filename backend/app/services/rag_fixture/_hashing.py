"""Hashing / signing utilities shared across RAG quality modules.

Phase 06-03 (D-01..D-03, D-11, D-15). Split out of the former
monolithic ``app/services/rag_fixture.py``. Imported by 8+ chunking modules,
``rag_quality.py`` and the quality worker — treat as a public utility surface.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from app.schemas.eval import (
    CANONICALIZATION_VERSION,
    SCHEMA_VERSION_RAG_QUALITY,
    FailClosedResult,
)


def stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    """Canonical content hash for a chunk body."""
    return text_hash(text)


def quote_hash(quote: str) -> str:
    return text_hash(quote)


def prompt_file_hash(path: str | Path) -> str:
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def schema_contract_hash() -> str:
    """Stable hash of the rag-quality schema contract version string."""
    return stable_hash(
        {
            "schema_version": SCHEMA_VERSION_RAG_QUALITY,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "fields": [
                "SourceSnapshot",
                "EvidenceRef",
                "EvalCase",
                "ModelLineage",
                "JudgeFixtureVerdict",
                "CalibrationSuite",
            ],
        }
    )


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    body = json.dumps(
        {k: v for k, v in payload.items() if k != "signature"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_signature(payload: dict[str, Any], secret: str) -> bool:
    expected = sign_payload(payload, secret)
    actual = payload.get("signature") or ""
    return bool(actual) and hmac.compare_digest(str(actual), expected)


def fail_closed(
    status: str,
    reason: str,
    *,
    detail: dict[str, Any] | None = None,
) -> FailClosedResult:
    return FailClosedResult(
        status=status,  # type: ignore[arg-type]
        metrics=None,
        quality_comparable=False,
        reason=reason,
        detail=detail or {},
    )
