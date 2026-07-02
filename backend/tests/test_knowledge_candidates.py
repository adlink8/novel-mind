"""Candidate package tests for the knowledge graph middle layer."""

from app.services.knowledge.candidates import (
    CandidateRecallConfig,
    CandidateRecallService,
    ChunkEvidence,
)


def _chunk(
    chunk_id: int,
    *,
    chapter_id: int = 1,
    chunk_index: int = 0,
    content: str = "刘备与关羽在桃园结义。",
    metadata_json: dict | None = None,
) -> ChunkEvidence:
    return ChunkEvidence(
        chunk_id=chunk_id,
        novel_id=1,
        chapter_id=chapter_id,
        chapter_title=f"第{chapter_id}章",
        chunk_index=chunk_index,
        content=content,
        chunk_type="narration",
        word_count=len(content),
        metadata_json=metadata_json or {},
    )


def test_candidate_package_bounds_evidence_and_separates_recall_from_confidence():
    service = CandidateRecallService()
    chunks = [
        _chunk(
            10,
            chunk_index=0,
            content="刘备与关羽在桃园结义，决定共同起兵。" * 80,
            metadata_json={"characters": ["刘备", "关羽"]},
        ),
        _chunk(
            11,
            chunk_index=1,
            content="关羽随后追随刘备，二人继续筹划行动。",
            metadata_json={"characters": ["刘备", "关羽"]},
        ),
    ]

    drafts = service.build_drafts_from_chunks(
        chunks=chunks,
        domain_profile="fiction",
        limit=1,
        signal_rows={
            10: {"bm25": {"score": 2.4}},
            11: {"vector": {"score": 0.88}},
        },
        config=CandidateRecallConfig(max_excerpt_chars=120),
    )

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.relation_type == "precedes"
    assert draft.source_kind == "text_chunk"
    assert draft.target_kind == "text_chunk"
    assert "confidence" not in draft.recall_signals
    assert set(draft.recall_signals["entity_overlap"]["shared"]) == {"刘备", "关羽"}
    assert draft.recall_signals["retrieval"]["source"]["bm25"]["score"] == 2.4

    from app.services.knowledge.evidence import build_evidence_package

    package = build_evidence_package(
        candidate=draft,
        evidence_chunks=chunks,
        domain_profile="fiction",
        max_excerpt_chars=120,
    )
    assert package["allowed_evidence_ids"] == ["ev-chunk-10", "ev-chunk-11"]
    assert package["candidate"]["evidence_refs"] == package["allowed_evidence_ids"]
    assert "confidence" not in package["candidate"]
    assert len(package["evidence"][0]["excerpt"]) <= 120
    assert "romantic" in package["allowed_relation_types"]
    assert package["llm_contract"]["must_cite_only_allowed_evidence_ids"] is True


def test_history_profile_adds_history_labels_and_time_window_signal():
    service = CandidateRecallService()
    chunks = [
        _chunk(
            20,
            chunk_index=0,
            content="公元190年，各路诸侯开始讨伐董卓。",
            metadata_json={"entities": ["董卓"], "time_refs": ["190"]},
        ),
        _chunk(
            21,
            chunk_index=1,
            content="190年后，联盟内部出现矛盾。",
            metadata_json={"entities": ["董卓"], "time_refs": ["190"]},
        ),
    ]

    drafts = service.build_drafts_from_chunks(
        chunks=chunks,
        domain_profile="history",
        limit=1,
    )

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.relation_type == "preceded"
    assert draft.recall_signals["time_window"]["shared"] == ["190"]

    from app.services.knowledge.evidence import build_evidence_package

    package = build_evidence_package(
        candidate=draft,
        evidence_chunks=chunks,
        domain_profile="history",
    )
    assert "allied_with" in package["allowed_relation_types"]
    assert "romantic" not in package["allowed_relation_types"]


def test_candidate_generation_respects_limit_and_uses_adjacency_as_signal_only():
    service = CandidateRecallService()
    chunks = [
        _chunk(1, chunk_index=0, metadata_json={"characters": ["A"]}),
        _chunk(2, chunk_index=1, metadata_json={"characters": ["A"]}),
        _chunk(3, chunk_index=2, metadata_json={"characters": ["A"]}),
    ]

    drafts = service.build_drafts_from_chunks(
        chunks=chunks,
        domain_profile="fiction",
        limit=1,
        config=CandidateRecallConfig(adjacency_window=2),
    )

    assert len(drafts) == 1
    assert drafts[0].recall_signals["adjacency"]["same_chapter"] is True
    assert drafts[0].recall_signals["adjacency"]["chunk_distance"] == 1
