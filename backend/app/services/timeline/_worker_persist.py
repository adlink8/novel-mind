"""Timeline worker chapter persistence + character FK fail-soft.

Responsibilities of this leaf module (refactor split):
- ``_persist_chapter`` idempotently writes ``MachineTimelineEvent`` +
  ``TimelineParticipant`` + ``TimelineEvidenceRef`` rows and the completed
  ``AnalysisChapterStage`` checkpoint.
- Character identity handling: ``_load_character_ids`` /
  ``_load_character_registry`` reads, and ``_sanitize_participant_entity_ids``
  fail-softs LLM-invented ``entity_id`` values (no corresponding characters
  row in this novel scope) to None — mention text is always preserved.

This module depends only on models/schemas — it never imports the worker
facade, so no import cycle. Public names are re-exported from ``worker.py``
unchanged.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.analysis import AnalysisChapterStage
from app.models.character import Character
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineEvidenceRef,
    TimelineParticipant,
)
from app.schemas.timeline import TimelineExtraction


async def _load_character_ids(
    sessions: async_sessionmaker[AsyncSession], novel_id: int
) -> set[int]:
    """该 novel 已注册的 characters 主键集合（用于 TimelineParticipant FK 校验）。"""
    async with sessions() as session:
        rows = await session.scalars(
            select(Character.id).where(Character.novel_id == novel_id)
        )
        return set(rows.all())


def _sanitize_participant_entity_ids(
    extraction: TimelineExtraction, known_ids: set[int]
) -> None:
    """FK fail-soft：把 LLM 臆造的 entity_id（novel 无对应 characters 行）置 None。

    对齐 relationships/candidates 的 fail-soft 模式：永不信任 LLM 给出的角色 id。
    只校验 id 存在性（本 novel scope）；mention 文本始终保留。
    """
    for event in extraction.events:
        for item in event.participants:
            if item.entity_id is not None and item.entity_id not in known_ids:
                item.entity_id = None


async def _load_character_registry(
    sessions: async_sessionmaker[AsyncSession], novel_id: int
) -> list[dict[str, Any]]:
    """该 novel 的 characters 注册表（id + name + aliases），随证据一起喂给抽取模型。"""
    async with sessions() as session:
        rows = (
            await session.execute(
                select(Character.id, Character.name, Character.aliases).where(
                    Character.novel_id == novel_id
                )
            )
        ).all()
    registry = []
    for row in rows:
        aliases = [
            alias.strip() for alias in (row.aliases or "").split(",") if alias.strip()
        ]
        registry.append({"id": row.id, "name": row.name, "aliases": aliases})
    return registry


async def _persist_chapter(
    sessions, run, version, chapter, stage_key, extraction
) -> None:
    known_character_ids = await _load_character_ids(sessions, run.novel_id)
    _sanitize_participant_entity_ids(extraction, known_character_ids)
    artifact = extraction.model_dump_json(exclude_none=False)
    checksum = hashlib.sha256(artifact.encode()).hexdigest()
    async with sessions.begin() as session:
        existing = await session.scalar(
            select(AnalysisChapterStage)
            .where(
                AnalysisChapterStage.run_id == run.id,
                AnalysisChapterStage.stage_key == stage_key,
            )
            .with_for_update()
        )
        if existing is not None and existing.status == "completed":
            return
        for candidate in extraction.events:
            logical_id = f"{chapter.id}:{candidate.candidate_id}"
            event = MachineTimelineEvent(
                version_id=version.id,
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                logical_event_id=logical_id,
                title=candidate.title,
                description=candidate.description,
                event_type=candidate.event_type,
                time_precision=candidate.story_time.precision,
                time_expression=candidate.story_time.expression,
                exact_time=candidate.story_time.exact_time,
                relative_anchor_event_id=candidate.story_time.anchor_event_id,
                relative_relation=candidate.story_time.relation,
                fuzzy_start=candidate.story_time.fuzzy_start,
                fuzzy_end=candidate.story_time.fuzzy_end,
                narrative_chapter_number=chapter.chapter_number,
                narrative_index=candidate.narrative_index,
                story_rank=None,
                story_constraints=[],
                confidence=candidate.confidence,
                prompt_hash=version.prompt_hash,
                schema_hash=version.schema_hash,
                model_lineage={
                    "stage": "chapter_extract",
                    "deployment": version.model_lineage["chapter_extract"],
                },
                publication_status="provisional",
            )
            session.add(event)
            await session.flush()
            session.add_all(
                [
                    TimelineParticipant(
                        event_id=event.id,
                        entity_id=item.entity_id,
                        mention=item.mention,
                    )
                    for item in candidate.participants
                ]
            )
            session.add_all(
                [
                    TimelineEvidenceRef(
                        event_id=event.id,
                        chapter_id=ref.chapter_id,
                        evidence_id=ref.evidence_id,
                        source_start=ref.source_start,
                        source_end=ref.source_end,
                        content_hash=ref.content_hash,
                    )
                    for ref in candidate.evidence
                ]
            )
        checkpoint = {
            "gateway_output": json.loads(artifact),
            "artifact_checksum": checksum,
        }
        if existing is None:
            session.add(
                AnalysisChapterStage(
                    run_id=run.id,
                    chapter_id=chapter.id,
                    stage_key=stage_key,
                    status="completed",
                    artifact_checksum=checksum,
                    checkpoint=checkpoint,
                )
            )
        else:
            existing.status = "completed"
            existing.artifact_checksum = checksum
            existing.checkpoint = checkpoint
