"""Evidence package helpers for LLM-bounded knowledge judgments."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.models.knowledge import (
    RELATION_TYPES_BY_DOMAIN_PROFILE,
    KnowledgeEvidenceRef,
)

EVIDENCE_PACKAGE_VERSION = "knowledge-evidence-package.v1"
DEFAULT_EXCERPT_CHARS = 700


def evidence_ref_key_for_chunk(chunk_id: int) -> str:
    """Return the only evidence ID format supplied to the LLM."""

    return f"ev-chunk-{chunk_id}"


def bounded_excerpt(content: str, max_chars: int = DEFAULT_EXCERPT_CHARS) -> str:
    """Collapse whitespace and cap text passed to the LLM."""

    normalized = " ".join((content or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3]}..."


def _chunk_to_evidence_item(chunk: Any, max_chars: int) -> dict[str, Any]:
    metadata = getattr(chunk, "metadata_json", None) or {}
    chunk_id = int(getattr(chunk, "chunk_id", getattr(chunk, "id", 0)))
    chapter_id = getattr(chunk, "chapter_id", None)
    return {
        "evidence_id": evidence_ref_key_for_chunk(chunk_id),
        "source_type": "text_chunk",
        "chunk_id": chunk_id,
        "chapter_id": chapter_id,
        "chapter_title": getattr(chunk, "chapter_title", "") or "",
        "chunk_index": getattr(chunk, "chunk_index", None),
        "excerpt": bounded_excerpt(getattr(chunk, "content", "") or "", max_chars),
        "metadata": {
            "chunk_type": getattr(chunk, "chunk_type", None),
            "word_count": getattr(chunk, "word_count", None),
            "entities": metadata.get("entities")
            or metadata.get("characters")
            or metadata.get("aliases")
            or [],
            "time_refs": metadata.get("time_refs")
            or metadata.get("times")
            or metadata.get("dates")
            or metadata.get("time")
            or [],
        },
    }


def build_evidence_package(
    *,
    candidate: Any,
    evidence_chunks: list[Any],
    domain_profile: str,
    ontology_profile: str | None = None,
    max_excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
) -> dict[str, Any]:
    """Build the bounded evidence package passed to the LLM.

    The package contains recall signals and allowed evidence IDs, but no field
    named relation confidence. Confidence is reserved for LLM judgment output.
    """

    if domain_profile not in RELATION_TYPES_BY_DOMAIN_PROFILE:
        raise ValueError(f"Unsupported domain_profile: {domain_profile}")

    ontology = ontology_profile or f"{domain_profile}.v1"
    evidence = [
        _chunk_to_evidence_item(chunk, max_excerpt_chars) for chunk in evidence_chunks
    ]
    allowed_evidence_ids = [item["evidence_id"] for item in evidence]

    candidate_payload = {
        "candidate_id": int(
            getattr(candidate, "candidate_id", getattr(candidate, "id", 0))
        ),
        "relation_type": getattr(candidate, "relation_type", ""),
        "source": {
            "kind": getattr(candidate, "source_kind", "text_chunk"),
            "id": int(getattr(candidate, "source_id", 0)),
        },
        "target": {
            "kind": getattr(candidate, "target_kind", "text_chunk"),
            "id": int(getattr(candidate, "target_id", 0)),
        },
        "recall_signals": getattr(candidate, "recall_signals", {}) or {},
        "evidence_refs": allowed_evidence_ids,
    }

    return {
        "package_version": EVIDENCE_PACKAGE_VERSION,
        "domain_profile": domain_profile,
        "ontology_profile": ontology,
        "allowed_relation_types": list(
            RELATION_TYPES_BY_DOMAIN_PROFILE[domain_profile]
        ),
        "allowed_evidence_ids": allowed_evidence_ids,
        "candidate": candidate_payload,
        "evidence": evidence,
        "llm_contract": {
            "json_only": True,
            "must_cite_only_allowed_evidence_ids": True,
            "unsupported_claims_require_rejection_or_review": True,
        },
    }


def package_to_snapshot(package: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe package snapshot for candidate audit fields."""

    def _clean(value: Any) -> Any:
        if hasattr(value, "__dataclass_fields__"):
            return asdict(value)
        if isinstance(value, dict):
            return {str(k): _clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_clean(v) for v in value]
        return value

    return _clean(package)


def evidence_ref_from_package_item(
    *,
    owner_id: int,
    novel_id: int,
    run_id: int,
    item: dict[str, Any],
) -> KnowledgeEvidenceRef:
    """Create an ORM evidence row from one package evidence item."""

    return KnowledgeEvidenceRef(
        owner_id=owner_id,
        novel_id=novel_id,
        run_id=run_id,
        ref_key=item["evidence_id"],
        source_type=item["source_type"],
        text_chunk_id=item.get("chunk_id"),
        chapter_id=item.get("chapter_id"),
        source_locator={
            "chunk_id": item.get("chunk_id"),
            "chapter_id": item.get("chapter_id"),
            "chunk_index": item.get("chunk_index"),
        },
        excerpt=item.get("excerpt"),
        metadata_json=item.get("metadata", {}),
    )
