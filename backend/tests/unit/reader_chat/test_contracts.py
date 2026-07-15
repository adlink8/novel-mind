"""Phase 10 reader-chat strict contract and ORM invariant tests (Wave 0)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64

AUTHORITY_TABLES = {
    "reader_conversations",
    "reader_messages",
    "reader_message_selections",
    "reader_context_manifests",
    "reader_context_evidence_refs",
    "reader_message_citations",
    "reader_generation_jobs",
    "reader_model_call_attempts",
    "reader_budget_ledgers",
    "reader_budget_reservations",
}

# Chat must never project into domain-fact tables as parent/source FKs.
FORBIDDEN_CHAT_FK_TARGETS = {
    "machine_timeline_events",
    "timeline_events",
    "timeline_evidence_refs",
    "relationship_observations",
    "relationship_observation_candidates",
    "character_relations",
    "knowledge_event_candidates",
    "narrative_units",
}


def test_authority_tables_are_registered_on_metadata():
    from app.models.reader_chat import ReaderConversation

    tables = set(ReaderConversation.metadata.tables)
    assert AUTHORITY_TABLES <= tables


def test_orm_exports_cover_all_reader_chat_entities():
    from app.models import (
        ReaderBudgetLedger,
        ReaderBudgetReservation,
        ReaderContextEvidenceRef,
        ReaderContextManifest,
        ReaderConversation,
        ReaderGenerationJob,
        ReaderMessage,
        ReaderMessageCitation,
        ReaderMessageSelection,
        ReaderModelCallAttempt,
    )

    assert ReaderConversation.__tablename__ == "reader_conversations"
    assert ReaderMessage.__tablename__ == "reader_messages"
    assert ReaderMessageSelection.__tablename__ == "reader_message_selections"
    assert ReaderContextManifest.__tablename__ == "reader_context_manifests"
    assert ReaderContextEvidenceRef.__tablename__ == "reader_context_evidence_refs"
    assert ReaderMessageCitation.__tablename__ == "reader_message_citations"
    assert ReaderGenerationJob.__tablename__ == "reader_generation_jobs"
    assert ReaderModelCallAttempt.__tablename__ == "reader_model_call_attempts"
    assert ReaderBudgetLedger.__tablename__ == "reader_budget_ledgers"
    assert ReaderBudgetReservation.__tablename__ == "reader_budget_reservations"


def test_user_message_owns_selection_and_manifest_uniqueness():
    from app.models.reader_chat import ReaderContextManifest, ReaderMessageSelection

    sel_cols = set(inspect(ReaderMessageSelection).columns.keys())
    man_cols = set(inspect(ReaderContextManifest).columns.keys())
    assert {
        "user_message_id",
        "chapter_id",
        "source_start",
        "source_end",
        "selection_text",
        "selection_text_hash",
        "chapter_content_hash",
    } <= sel_cols
    assert {
        "user_message_id",
        "manifest_checksum",
        "cutoff_chapter_number",
        "full_book",
        "reading_progress_snapshot",
    } <= man_cols

    sel_unique = {
        tuple(c.name for c in u.columns)
        for u in ReaderMessageSelection.__table__.constraints
        if getattr(u, "columns", None) is not None and u.__class__.__name__ == "UniqueConstraint"
    }
    # UniqueConstraint or unique Index on user_message_id
    col = ReaderMessageSelection.__table__.c.user_message_id
    assert col.unique or any("user_message_id" in cols for cols in sel_unique) or any(
        idx.unique and "user_message_id" in idx.columns
        for idx in ReaderMessageSelection.__table__.indexes
    )

    man_unique = {
        tuple(c.name for c in u.columns)
        for u in ReaderContextManifest.__table__.constraints
        if getattr(u, "columns", None) is not None
        and u.__class__.__name__ == "UniqueConstraint"
    }
    man_col = ReaderContextManifest.__table__.c.user_message_id
    assert man_col.unique or any("user_message_id" in cols for cols in man_unique) or any(
        idx.unique and "user_message_id" in idx.columns
        for idx in ReaderContextManifest.__table__.indexes
    )


def test_selection_offset_and_hash_columns_are_non_negative_ranges():
    from app.models.reader_chat import ReaderMessageSelection

    check_names = {c.name for c in ReaderMessageSelection.__table__.constraints if hasattr(c, "name")}
    assert "ck_reader_selection_offsets" in check_names
    # SHA-256 fields are 64-char hex strings
    assert ReaderMessageSelection.__table__.c.selection_text_hash.type.length == 64
    assert ReaderMessageSelection.__table__.c.chapter_content_hash.type.length == 64


def test_message_sequence_and_client_idempotency_uniqueness():
    from app.models.reader_chat import ReaderMessage

    cols = set(inspect(ReaderMessage).columns.keys())
    assert {
        "conversation_id",
        "owner_id",
        "novel_id",
        "sequence",
        "role",
        "body",
        "client_message_id",
        "reply_to_message_id",
    } <= cols

    unique_pairs = set()
    for u in ReaderMessage.__table__.constraints:
        if u.__class__.__name__ == "UniqueConstraint":
            unique_pairs.add(tuple(c.name for c in u.columns))
    for idx in ReaderMessage.__table__.indexes:
        if idx.unique:
            unique_pairs.add(tuple(idx.columns.keys()))
    assert ("conversation_id", "sequence") in unique_pairs
    assert ("conversation_id", "client_message_id") in unique_pairs


def test_citation_targets_context_evidence_ref_only():
    from app.models.reader_chat import ReaderMessageCitation

    cols = set(inspect(ReaderMessageCitation).columns.keys())
    assert {"assistant_message_id", "block_id", "context_evidence_ref_id"} <= cols
    fks = {
        (fk.parent.name, fk.column.table.name)
        for col in ReaderMessageCitation.__table__.columns
        for fk in col.foreign_keys
    }
    assert ("context_evidence_ref_id", "reader_context_evidence_refs") in fks
    assert ("assistant_message_id", "reader_messages") in fks


def test_generation_job_status_lease_cancel_retry_fields():
    from app.models.reader_chat import ReaderGenerationJob

    cols = set(inspect(ReaderGenerationJob).columns.keys())
    assert {
        "user_message_id",
        "status",
        "status_reason",
        "lease_id",
        "lease_expires_at",
        "heartbeat_at",
        "cancel_requested",
        "retry_count",
        "prompt_hash",
        "schema_hash",
        "context_manifest_checksum",
        "model_lineage",
    } <= cols
    check_names = {c.name for c in ReaderGenerationJob.__table__.constraints if hasattr(c, "name")}
    assert "ck_reader_gen_jobs_status" in check_names

    # Partial unique index for one nonterminal job per user message
    partial = [
        idx
        for idx in ReaderGenerationJob.__table__.indexes
        if idx.unique and "user_message_id" in idx.columns
    ]
    assert partial, "expected partial unique index on user_message_id for nonterminal jobs"
    assert any(getattr(idx, "dialect_options", {}).get("postgresql", {}).get("where") is not None
               or getattr(idx, "kwargs", None)
               for idx in partial) or any(
        getattr(idx, "dialect_options", {}) for idx in partial
    )
    # Dialect-agnostic: at least one unique index targets user_message_id with a where clause
    assert any(
        getattr(idx, "dialect_options", {}).get("postgresql", {}).get("where") is not None
        for idx in ReaderGenerationJob.__table__.indexes
        if idx.unique
    )


def test_dual_scope_budget_ledgers_and_reservation_usage():
    from app.models.reader_chat import ReaderBudgetLedger, ReaderBudgetReservation

    ledger_cols = set(inspect(ReaderBudgetLedger).columns.keys())
    assert {
        "scope_type",
        "owner_id",
        "novel_id",
        "conversation_id",
        "max_calls",
        "max_input_tokens",
        "max_output_tokens",
        "max_cost_usd",
        "reserved_calls",
        "reserved_input_tokens",
        "reserved_output_tokens",
        "reserved_cost_usd",
        "settled_calls",
        "settled_input_tokens",
        "settled_output_tokens",
        "settled_cost_usd",
    } <= ledger_cols
    check_names = {c.name for c in ReaderBudgetLedger.__table__.constraints if hasattr(c, "name")}
    assert "ck_reader_budget_scope_type" in check_names

    res_cols = set(inspect(ReaderBudgetReservation).columns.keys())
    assert {
        "ledger_id",
        "reservation_key",
        "status",
        "calls",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "settled_usage",
    } <= res_cols


def test_no_chat_fk_projects_into_domain_fact_tables():
    from app.models.reader_chat import ReaderConversation

    chat_tables = {
        name: table
        for name, table in ReaderConversation.metadata.tables.items()
        if name.startswith("reader_")
    }
    assert AUTHORITY_TABLES <= set(chat_tables)
    for name, table in chat_tables.items():
        for col in table.columns:
            for fk in col.foreign_keys:
                target = fk.column.table.name
                assert target not in FORBIDDEN_CHAT_FK_TARGETS, (
                    f"{name}.{col.name} must not FK to domain fact table {target}"
                )


def test_fiction_only_evidence_source_types():
    from app.models.reader_chat import READER_EVIDENCE_SOURCE_TYPES

    assert "selection" in READER_EVIDENCE_SOURCE_TYPES
    assert "hierarchy" in READER_EVIDENCE_SOURCE_TYPES
    assert "timeline" in READER_EVIDENCE_SOURCE_TYPES
    assert "knowledge" in READER_EVIDENCE_SOURCE_TYPES
    assert "relationship_observation" in READER_EVIDENCE_SOURCE_TYPES
    for forbidden in ("history", "historical", "clue", "foreshadow"):
        assert forbidden not in READER_EVIDENCE_SOURCE_TYPES


def _answer_payload(**overrides):
    payload = {
        "schema_version": "reader-answer.v1",
        "answer_blocks": [
            {
                "block_id": "b1",
                "text": "The envoy enters at dawn.",
                "evidence_refs": ["selection:1", "hierarchy:ev-2"],
            }
        ],
        "clarifying_question": None,
        "uncertainty": None,
        "suggestion_candidates": [],
    }
    payload.update(overrides)
    return payload


def test_reader_answer_envelope_accepts_strict_cited_blocks():
    from app.schemas.reader_chat import ReaderAnswerEnvelope

    env = ReaderAnswerEnvelope.model_validate(_answer_payload())
    assert env.schema_version == "reader-answer.v1"
    assert env.answer_blocks[0].evidence_refs[0] == "selection:1"


def test_reader_answer_envelope_rejects_uncited_blocks():
    from app.schemas.reader_chat import ReaderAnswerEnvelope

    with pytest.raises(ValidationError):
        ReaderAnswerEnvelope.model_validate(
            _answer_payload(
                answer_blocks=[{"block_id": "b1", "text": "claim", "evidence_refs": []}]
            )
        )


def test_reader_answer_envelope_rejects_extra_fields():
    from app.schemas.reader_chat import ReaderAnswerEnvelope

    bad = _answer_payload()
    bad["write_to_database"] = True
    with pytest.raises(ValidationError):
        ReaderAnswerEnvelope.model_validate(bad)


def test_reader_answer_envelope_rejects_unknown_refs_at_business_validation():
    from app.schemas.reader_chat import ReaderAnswerEnvelope, validate_answer_against_manifest

    env = ReaderAnswerEnvelope.model_validate(
        _answer_payload(
            answer_blocks=[
                {
                    "block_id": "b1",
                    "text": "claim",
                    "evidence_refs": ["selection:1", "forged:99"],
                }
            ]
        )
    )
    with pytest.raises(ValueError):
        validate_answer_against_manifest(env, allowed_evidence_ids={"selection:1"})


def test_suggestion_requires_literal_confirmation_true():
    from app.schemas.reader_chat import ReaderAnswerEnvelope, SuggestionCandidate

    ok = SuggestionCandidate.model_validate(
        {
            "candidate_type": "timeline",
            "target_ref": None,
            "proposal": "Mark this as a plot beat",
            "evidence_refs": ["selection:1"],
            "requires_explicit_confirmation": True,
        }
    )
    assert ok.requires_explicit_confirmation is True

    with pytest.raises(ValidationError):
        SuggestionCandidate.model_validate(
            {
                "candidate_type": "relationship",
                "proposal": "Add ally edge",
                "evidence_refs": ["selection:1"],
                "requires_explicit_confirmation": False,
            }
        )

    env = ReaderAnswerEnvelope.model_validate(
        _answer_payload(
            answer_blocks=[],
            uncertainty={
                "reason_code": "insufficient_evidence",
                "explanation": "No usable evidence.",
                "missing_evidence": ["timeline context"],
            },
            suggestion_candidates=[
                {
                    "candidate_type": "clue",
                    "target_ref": None,
                    "proposal": "Track as foreshadow candidate",
                    "evidence_refs": ["selection:1"],
                    "requires_explicit_confirmation": True,
                }
            ],
        )
    )
    assert env.suggestion_candidates[0].requires_explicit_confirmation is True


def test_no_evidence_requires_uncertainty_or_clarification():
    from app.schemas.reader_chat import ReaderAnswerEnvelope

    with pytest.raises(ValidationError):
        ReaderAnswerEnvelope.model_validate(
            _answer_payload(answer_blocks=[], clarifying_question=None, uncertainty=None)
        )

    env = ReaderAnswerEnvelope.model_validate(
        _answer_payload(
            answer_blocks=[],
            clarifying_question="Which character did you mean?",
            uncertainty=None,
        )
    )
    assert env.clarifying_question is not None


def test_selection_request_rejects_negative_or_inverted_offsets():
    from app.schemas.reader_chat import SelectionCoordinate

    ok = SelectionCoordinate.model_validate(
        {
            "chapter_id": 1,
            "source_start": 0,
            "source_end": 12,
            "selection_text": "hello world!",
            "selection_text_hash": HEX64,
            "chapter_content_hash": HEX64_B,
        }
    )
    assert ok.source_end > ok.source_start

    with pytest.raises(ValidationError):
        SelectionCoordinate.model_validate(
            {
                "chapter_id": 1,
                "source_start": -1,
                "source_end": 5,
                "selection_text": "x",
                "selection_text_hash": HEX64,
                "chapter_content_hash": HEX64_B,
            }
        )
    with pytest.raises(ValidationError):
        SelectionCoordinate.model_validate(
            {
                "chapter_id": 1,
                "source_start": 5,
                "source_end": 5,
                "selection_text": "",
                "selection_text_hash": HEX64,
                "chapter_content_hash": HEX64_B,
            }
        )


def test_api_list_schemas_are_metadata_only():
    from app.schemas.reader_chat import ConversationListItem

    fields = set(ConversationListItem.model_fields)
    assert "title" in fields
    assert "status" in fields
    # List must not expose message bodies or evidence excerpts
    assert "body" not in fields
    assert "selection_text" not in fields
    assert "excerpt" not in fields
