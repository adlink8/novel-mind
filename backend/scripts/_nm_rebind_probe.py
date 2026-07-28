"""Probe: transport output → rebind_chapter_state_package."""
from __future__ import annotations

import asyncio
import logging
import traceback

logging.disable(logging.WARNING)


async def main() -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.models.chunk_build import ChunkHierarchyNode
    from app.models.narrative_memory import NarrativeMemoryVersion
    from app.models.novel import Chapter
    from app.services.narrative_memory.builder_packages import (
        build_chapter_state_input,
        load_chapter_evidence_leaves,
        rebind_chapter_state_package,
    )
    from app.services.narrative_memory.contracts import ModelLineage
    from scripts.run_narrative_memory_build import (
        _PROMPT_HASH,
        _SCHEMA_HASH,
        _DECODING_HASH,
        _CONFIG_HASH,
        _POLICY_HASH,
        _VertexNmTransport,
    )

    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    eng = create_async_engine(url)
    sessions = async_sessionmaker(eng, expire_on_commit=False)
    t = _VertexNmTransport(sessions, model=settings.vertex_model)

    async with sessions() as s:
        version = await s.get(NarrativeMemoryVersion, 1)
        ch = (
            await s.scalars(
                select(Chapter)
                .where(Chapter.novel_id == 91)
                .order_by(Chapter.chapter_number)
                .limit(1)
            )
        ).first()
        assert version is not None and ch is not None
        leaves = await load_chapter_evidence_leaves(
            s,
            hierarchy_build_id=version.hierarchy_build_id,
            novel_id=91,
            chapter_id=ch.id,
            chapter_number=ch.chapter_number,
            source_snapshot_hash=version.source_snapshot_hash,
        )
        lineage = ModelLineage.model_validate(version.model_lineage)
        input_pkg = build_chapter_state_input(
            version=version,
            chapter_id=ch.id,
            chapter_number=ch.chapter_number,
            evidence_leaves=leaves,
            optional_signals=(),
            prompt_hash=_PROMPT_HASH,
            schema_hash=_SCHEMA_HASH,
            model_lineage=lineage,
            decoding_hash=_DECODING_HASH,
            config_hash=_CONFIG_HASH,
            policy_hash=_POLICY_HASH,
        )
        payload = input_pkg.model_dump(mode="json")
        print("leaves", len(leaves), "chapter", ch.chapter_number)

    try:
        raw = await t.complete(
            stage_key=f"chapter_state:{payload['chapter_id']}",
            payload=payload,
            deployment={"model": settings.vertex_model},
            repair=False,
        )
        print("raw claims", len(raw.get("claims") or []))
        print("raw sample", raw.get("claims", [None])[0])
        print("bindings", raw.get("source_bindings"))
        pkg = rebind_chapter_state_package(input_package=input_pkg, model_output=raw)
        print("OK package nodes", len(pkg.nodes), "claims", len(pkg.claims))
    except Exception:
        traceback.print_exc()
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
