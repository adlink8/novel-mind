"""Exact selection validation and immutable spoiler-safe context manifests.

Server authority: client selection text/offsets/hashes are claims. Visible
context is frozen at send time; retry reuses the original checksum-addressed
manifest rather than rebuilding under a newer reading progress.

拆分说明（refactor split）：本模块保留为门面 —— 全部顶层符号从 4 个按职责
域拆分的同目录模块显式 re-export，``from app.services.reader_chat.context
import X`` 的 import surface 完全不变：

- ``context_types``：数据契约层（frozen dataclass / ``SelectionValidationError`` /
  codepoint 与 hash 工具，叶模块，无 DB 依赖）；
- ``context_validation``：校验层（``validate_selection`` /
  ``validate_chapter_context`` / ``validate_chapter_range_context``）；
- ``context_queryplan``：QueryPlan 消费适配层（``resolve_progress_snapshot`` /
  ``build_reader_consumer_request`` / ``run_reader_queryplan`` /
  ``_build_world_projection_resolver``，含 blocked-code 映射）；
- ``context_manifest``：manifest 装配层（``assemble_context_manifest`` /
  ``assemble_range_context_manifest`` / ``freeze_manifest_from_stored`` /
  ``assert_retry_uses_original_checksum``）。
"""

from __future__ import annotations

from .context_manifest import (
    _dialogue_framing,
    assert_retry_uses_original_checksum,
    assemble_context_manifest,
    assemble_range_context_manifest,
    freeze_manifest_from_stored,
)
from .context_queryplan import (
    _build_world_projection_resolver,
    _consumer_blocked_code,
    build_reader_consumer_request,
    resolve_progress_snapshot,
    run_reader_queryplan,
)
from .context_types import (
    CHAPTER_EVIDENCE_KEY,
    CHAPTER_RANGE_ANCHOR_KIND,
    MAX_RANGE_CONTEXT_CODE_POINTS,
    MAX_SELECTION_CODE_POINTS,
    SELECTION_EVIDENCE_KEY,
    ContextEvidenceEntry,
    ContextManifest,
    ProgressSnapshot,
    SelectionValidationError,
    ValidatedChapterRange,
    ValidatedChapterSegment,
    ValidatedSelection,
    canonical_json_bytes,
    canonical_manifest_checksum,
    code_point_len,
    code_point_slice,
    content_sha256,
)
from .context_validation import (
    chapter_range_budget,
    narrow_chapter_range,
    validate_chapter_context,
    validate_chapter_range_context,
    validate_selection,
)

__all__ = [
    "MAX_SELECTION_CODE_POINTS",
    "SELECTION_EVIDENCE_KEY",
    "CHAPTER_EVIDENCE_KEY",
    "MAX_RANGE_CONTEXT_CODE_POINTS",
    "CHAPTER_RANGE_ANCHOR_KIND",
    "SelectionValidationError",
    "ValidatedSelection",
    "ProgressSnapshot",
    "ValidatedChapterSegment",
    "ValidatedChapterRange",
    "narrow_chapter_range",
    "chapter_range_budget",
    "ContextEvidenceEntry",
    "ContextManifest",
    "code_point_len",
    "code_point_slice",
    "content_sha256",
    "canonical_json_bytes",
    "canonical_manifest_checksum",
    "resolve_progress_snapshot",
    "_consumer_blocked_code",
    "build_reader_consumer_request",
    "_build_world_projection_resolver",
    "run_reader_queryplan",
    "validate_selection",
    "validate_chapter_context",
    "validate_chapter_range_context",
    "assemble_context_manifest",
    "assemble_range_context_manifest",
    "assert_retry_uses_original_checksum",
    "freeze_manifest_from_stored",
    "_dialogue_framing",
]
