"""Deterministic candidate recall for knowledge graph relation packages."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import bindparam, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import RELATION_TYPES_BY_DOMAIN_PROFILE
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.services.ai_service import ai_service
from app.services.knowledge.evidence import build_evidence_package
from app.services.vector_store import vector_store

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChunkEvidence:
    """A text chunk plus metadata used for deterministic recall."""

    chunk_id: int
    novel_id: int
    chapter_id: int | None
    chapter_title: str
    chunk_index: int
    content: str
    chunk_type: str | None = None
    word_count: int | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RelationCandidateDraft:
    """Potential relation from recall signals, never an accepted graph fact."""

    candidate_id: int
    novel_id: int
    domain_profile: str
    relation_type: str
    source_kind: str
    source_id: int
    target_kind: str
    target_id: int
    recall_signals: dict[str, Any]
    evidence_refs: list[str]
    package_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CandidateRecallConfig:
    """Candidate recall knobs for CLI and tests."""

    top_k: int = 10
    adjacency_window: int = 1
    nearby_chapter_window: int = 1
    max_excerpt_chars: int = 700
    vector_enabled: bool = True


class CandidateRecallService:
    """Build candidate packages using recall signals only."""

    def __init__(self):
        self.vector_store = vector_store
        self.ai_service = ai_service

    async def build_candidate_packages(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
        domain_profile: str,
        limit: int,
        query: str | None = None,
        owner_id: int | None = None,
        ontology_profile: str | None = None,
        config: CandidateRecallConfig | None = None,
    ) -> list[tuple[RelationCandidateDraft, list[ChunkEvidence], dict[str, Any]]]:
        """Generate bounded evidence packages for relation judgment."""

        if domain_profile not in RELATION_TYPES_BY_DOMAIN_PROFILE:
            raise ValueError(f"Unsupported domain_profile: {domain_profile}")

        cfg = config or CandidateRecallConfig(top_k=max(limit, 1))
        novel = await self._load_novel(db, novel_id=novel_id, owner_id=owner_id)
        chunks = await self._load_chunks(db, novel_id=novel.id)
        if len(chunks) < 2:
            return []

        signal_rows = await self._collect_signal_rows(
            db,
            novel_id=novel.id,
            query=query,
            domain_profile=domain_profile,
            top_k=max(limit, cfg.top_k),
            vector_enabled=cfg.vector_enabled,
        )
        drafts = self.build_drafts_from_chunks(
            chunks=chunks,
            domain_profile=domain_profile,
            limit=limit,
            signal_rows=signal_rows,
            config=cfg,
        )

        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        packages = []
        for draft in drafts:
            evidence_chunks = [
                by_id[chunk_id]
                for chunk_id in (draft.source_id, draft.target_id)
                if chunk_id in by_id
            ]
            package = build_evidence_package(
                candidate=draft,
                evidence_chunks=evidence_chunks,
                domain_profile=domain_profile,
                ontology_profile=ontology_profile,
                max_excerpt_chars=cfg.max_excerpt_chars,
            )
            draft.package_snapshot = package
            draft.evidence_refs = package["allowed_evidence_ids"]
            packages.append((draft, evidence_chunks, package))
        return packages

    def build_drafts_from_chunks(
        self,
        *,
        chunks: list[ChunkEvidence],
        domain_profile: str,
        limit: int,
        signal_rows: dict[int, dict[str, Any]] | None = None,
        config: CandidateRecallConfig | None = None,
    ) -> list[RelationCandidateDraft]:
        """Create relation drafts from chunks and deterministic signals."""

        if domain_profile not in RELATION_TYPES_BY_DOMAIN_PROFILE:
            raise ValueError(f"Unsupported domain_profile: {domain_profile}")
        if limit <= 0:
            return []

        cfg = config or CandidateRecallConfig(top_k=limit)
        signals_by_chunk = signal_rows or {}
        relation_type = "precedes" if domain_profile == "fiction" else "preceded"
        sorted_chunks = sorted(
            chunks,
            key=lambda c: (
                c.chapter_id if c.chapter_id is not None else -1,
                c.chunk_index,
                c.chunk_id,
            ),
        )

        drafts: list[RelationCandidateDraft] = []
        seen_pairs: set[tuple[int, int]] = set()
        for idx, source in enumerate(sorted_chunks):
            for offset in range(1, cfg.adjacency_window + 1):
                neighbor_idx = idx + offset
                if neighbor_idx >= len(sorted_chunks):
                    continue
                target = sorted_chunks[neighbor_idx]
                if (source.chunk_id, target.chunk_id) in seen_pairs:
                    continue

                recall_signals = self._build_recall_signals(
                    source=source,
                    target=target,
                    source_signal=signals_by_chunk.get(source.chunk_id, {}),
                    target_signal=signals_by_chunk.get(target.chunk_id, {}),
                    domain_profile=domain_profile,
                    nearby_chapter_window=cfg.nearby_chapter_window,
                )
                if not recall_signals:
                    continue

                candidate_id = len(drafts) + 1
                drafts.append(
                    RelationCandidateDraft(
                        candidate_id=candidate_id,
                        novel_id=source.novel_id,
                        domain_profile=domain_profile,
                        relation_type=relation_type,
                        source_kind="text_chunk",
                        source_id=source.chunk_id,
                        target_kind="text_chunk",
                        target_id=target.chunk_id,
                        recall_signals=recall_signals,
                        evidence_refs=[],
                    )
                )
                seen_pairs.add((source.chunk_id, target.chunk_id))
                if len(drafts) >= limit:
                    return drafts
        return drafts

    async def _load_novel(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
        owner_id: int | None,
    ) -> Novel:
        stmt = select(Novel).where(Novel.id == novel_id)
        if owner_id is not None:
            stmt = stmt.where(Novel.owner_id == owner_id)
        result = await db.execute(stmt)
        novel = result.scalar_one_or_none()
        if novel is None:
            raise ValueError(f"Novel id={novel_id} not found or not owned by owner")
        return novel

    async def _load_chunks(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
    ) -> list[ChunkEvidence]:
        stmt = (
            select(
                TextChunk.id,
                TextChunk.novel_id,
                TextChunk.chapter_id,
                TextChunk.chunk_index,
                TextChunk.content,
                TextChunk.chunk_type,
                TextChunk.word_count,
                TextChunk.metadata_json,
                func.coalesce(Chapter.title, "").label("chapter_title"),
            )
            .outerjoin(Chapter, Chapter.id == TextChunk.chapter_id)
            .where(TextChunk.novel_id == novel_id)
            .order_by(TextChunk.chapter_id.asc(), TextChunk.chunk_index.asc(), TextChunk.id.asc())
        )
        result = await db.execute(stmt)
        return [
            ChunkEvidence(
                chunk_id=row.id,
                novel_id=row.novel_id,
                chapter_id=row.chapter_id,
                chapter_title=row.chapter_title or "",
                chunk_index=row.chunk_index,
                content=row.content,
                chunk_type=row.chunk_type,
                word_count=row.word_count,
                metadata_json=row.metadata_json or {},
            )
            for row in result.fetchall()
        ]

    async def _collect_signal_rows(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
        query: str | None,
        domain_profile: str,
        top_k: int,
        vector_enabled: bool,
    ) -> dict[int, dict[str, Any]]:
        signals: dict[int, dict[str, Any]] = {}
        if not query:
            return signals

        for row in await self._bm25_signal_rows(db, novel_id=novel_id, query=query, limit=top_k):
            signals.setdefault(row["chunk_id"], {})["bm25"] = {"score": row["score"]}

        if vector_enabled:
            for row in await self._vector_signal_rows(novel_id=novel_id, query=query, top_k=top_k):
                signals.setdefault(row["chunk_id"], {})["vector"] = {"score": row["score"]}

        if domain_profile == "history":
            for chunk_signal in signals.values():
                chunk_signal.setdefault("time_window", {"source": "metadata_or_text"})
        return signals

    async def _bm25_signal_rows(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        tsquery = func.plainto_tsquery("simple", bindparam("query"))
        rank = func.ts_rank_cd(TextChunk.search_vector, tsquery)
        stmt = (
            select(TextChunk.id.label("chunk_id"), rank.label("score"))
            .where(TextChunk.novel_id == bindparam("novel_id"))
            .where(TextChunk.search_vector.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(bindparam("limit"))
        )
        try:
            result = await db.execute(
                stmt,
                {"query": query, "novel_id": novel_id, "limit": limit},
            )
        except Exception as exc:
            logger.info("BM25 recall unavailable for novel_%s: %s", novel_id, exc)
            return []
        return [
            {"chunk_id": int(row.chunk_id), "score": float(row.score or 0.0)}
            for row in result.fetchall()
        ]

    async def _vector_signal_rows(
        self,
        *,
        novel_id: int,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        try:
            embeddings = await self.ai_service.embedding(texts=[query])
            if not embeddings:
                return []
            raw_rows = await self.vector_store.search(
                novel_id=novel_id,
                query_embedding=embeddings[0],
                top_k=top_k,
            )
        except Exception as exc:
            logger.info("Vector recall unavailable for novel_%s: %s", novel_id, exc)
            return []

        rows = []
        for row in raw_rows:
            chunk_id = _parse_chunk_id(row.get("chunk_id"))
            if chunk_id is None:
                continue
            rows.append({"chunk_id": chunk_id, "score": float(row.get("score", 0.0))})
        return rows

    def _build_recall_signals(
        self,
        *,
        source: ChunkEvidence,
        target: ChunkEvidence,
        source_signal: dict[str, Any],
        target_signal: dict[str, Any],
        domain_profile: str,
        nearby_chapter_window: int,
    ) -> dict[str, Any]:
        signals: dict[str, Any] = {}

        same_chapter = source.chapter_id is not None and source.chapter_id == target.chapter_id
        chapter_distance = _chapter_distance(source.chapter_id, target.chapter_id)
        chunk_distance = abs(target.chunk_index - source.chunk_index)
        if same_chapter or (
            chapter_distance is not None and chapter_distance <= nearby_chapter_window
        ):
            signals["adjacency"] = {
                "same_chapter": same_chapter,
                "chapter_distance": chapter_distance,
                "chunk_distance": chunk_distance,
            }

        shared_entities = sorted(_extract_entities(source) & _extract_entities(target))
        if shared_entities:
            signals["entity_overlap"] = {
                "shared": shared_entities,
                "source": "metadata_json",
            }

        if domain_profile == "history":
            source_times = _extract_time_refs(source)
            target_times = _extract_time_refs(target)
            shared_times = sorted(source_times & target_times)
            if shared_times:
                signals["time_window"] = {
                    "shared": shared_times,
                    "source": "metadata_json_or_text",
                }

        if source_signal or target_signal:
            signals["retrieval"] = {
                "source": source_signal,
                "target": target_signal,
            }

        return signals


def _parse_chunk_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.startswith("chunk_"):
        try:
            return int(value.replace("chunk_", "", 1))
        except ValueError:
            return None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _chapter_distance(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return abs(left - right)


def _metadata_values(metadata: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
    values: set[str] = set()
    for key in keys:
        raw = metadata.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            values.add(raw.strip())
        elif isinstance(raw, list):
            values.update(str(item).strip() for item in raw if str(item).strip())
        elif isinstance(raw, dict):
            values.update(str(item).strip() for item in raw.values() if str(item).strip())
    return {value for value in values if value}


def _extract_entities(chunk: ChunkEvidence) -> set[str]:
    return _metadata_values(
        chunk.metadata_json or {},
        ("entities", "characters", "aliases", "people", "persons", "organizations"),
    )


def _extract_time_refs(chunk: ChunkEvidence) -> set[str]:
    metadata_refs = _metadata_values(
        chunk.metadata_json or {},
        ("time_refs", "times", "dates", "date", "time", "year"),
    )
    text_refs = set(re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", chunk.content or ""))
    return metadata_refs | text_refs


candidate_recall_service = CandidateRecallService()
