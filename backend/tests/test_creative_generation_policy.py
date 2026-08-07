"""Phase 33-01 local context-package and candidate-only policy tests."""

import pytest
from pydantic import ValidationError

from app.schemas.creative_generation import CreativeContextPackage
from app.services.creative_generation_policy import (
    CreativeContextPolicyError,
    build_context_package,
    context_hash,
    validate_context_package,
)

pytestmark = pytest.mark.unit


def _evidence(novel_id: int = 11) -> dict:
    return {
        "evidence_key": "chunk:91:7",
        "novel_id": novel_id,
        "chapter_id": 3,
        "text_chunk_id": 7,
        "source_start": 0,
        "source_end": 12,
        "content_hash": "a" * 64,
    }


def _package() -> CreativeContextPackage:
    return build_context_package(
        owner_id=5,
        novel_id=11,
        project_id=19,
        cutoff_chapter_number=3,
        user_settings={"tone": "quiet"},
        original_evidence=[_evidence()],
        understanding_states=[
            {
                "state_kind": "chapter_state",
                "version_key": "nm-candidate-v1",
                "source_key": "chapter-state:3",
                "novel_id": 11,
                "chapter_number": 3,
            }
        ],
        override={
            "override_key": "choice:leave",
            "statement": "主人公选择离开",
            "reason": "用户创作决定",
            "original_evidence_key": "chunk:91:7",
        },
    )


def test_context_package_is_deterministically_hashed_and_candidate_only():
    package = _package()
    assert package.candidate_only is True
    assert package.output_space == "fanfiction_canon"
    assert package.context_hash == context_hash(package)
    assert package.context_hash == _package().context_hash


def test_context_package_scope_and_hash_are_checked():
    package = _package()
    assert validate_context_package(package, owner_id=5, novel_id=11) == package
    with pytest.raises(CreativeContextPolicyError, match="owner_scope"):
        validate_context_package(package, owner_id=6, novel_id=11)
    with pytest.raises(CreativeContextPolicyError, match="context_hash"):
        validate_context_package(
            package.model_copy(update={"context_hash": "b" * 64}),
            owner_id=5,
            novel_id=11,
        )


def test_evidence_and_state_lineage_cannot_cross_novel_or_cutoff_shape():
    with pytest.raises(ValidationError, match="package novel"):
        build_context_package(
            owner_id=5,
            novel_id=11,
            project_id=19,
            cutoff_chapter_number=3,
            original_evidence=[_evidence(novel_id=12)],
        )
    with pytest.raises(ValidationError, match="chapter_state requires"):
        build_context_package(
            owner_id=5,
            novel_id=11,
            project_id=19,
            cutoff_chapter_number=3,
            understanding_states=[
                {
                    "state_kind": "chapter_state",
                    "version_key": "v1",
                    "source_key": "state",
                    "novel_id": 11,
                }
            ],
        )


def test_original_evidence_is_leaf_only_and_fanfiction_space_is_rejected():
    with pytest.raises(ValidationError):
        build_context_package(
            owner_id=5,
            novel_id=11,
            project_id=19,
            cutoff_chapter_number=3,
            original_evidence=[{**_evidence(), "space": "fanfiction_canon"}],
        )
    with pytest.raises(ValidationError):
        build_context_package(
            owner_id=5,
            novel_id=11,
            project_id=19,
            cutoff_chapter_number=3,
            original_evidence=[{**_evidence(), "source_end": 0}],
        )


def test_extra_promotion_or_consumer_fields_are_forbidden():
    with pytest.raises(ValidationError):
        CreativeContextPackage(
            **_package().model_dump(),
            active_pointer_id=1,
        )
