"""Contract test: snapshot_manifest output must be JSON-serializable.

Regression for run 42 (313-chapter book): MachineTimelineEvent carries
DateTime columns (exact_time / fuzzy_start / fuzzy_end). Passing the ORM
values straight into the manifest dict made SQLAlchemy's JSON serializer
raise ``TypeError: Object of type datetime is not JSON serializable`` at
flush time, wrapped as StatementError — killing the whole run AFTER ~2h of
extraction. The manifest must round-trip like any JSON column read-back.
"""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.timeline.promotion import snapshot_manifest

pytestmark = pytest.mark.unit


async def _seed_scope(
    db_session: AsyncSession, *, exact_time: datetime | None
) -> int:
    from app.models.analysis import AnalysisVersion
    from app.models.timeline import MachineTimelineEvent

    owner = User(
        username="manifest-owner", email="manifest@example.com", hashed_password="x"
    )
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="manifest书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    version = AnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key="manifest-v1",
        status="candidate",
        source_snapshot_hash="a" * 64,
        hierarchy_build_id="build-1",
        hierarchy_checksum="b" * 64,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        model_lineage={},
        decoding_hash="e" * 64,
        config_hash="f" * 64,
        price_snapshot={},
        manifest={},
    )
    db_session.add(version)
    await db_session.flush()
    db_session.add(
        MachineTimelineEvent(
            version_id=version.id,
            owner_id=owner.id,
            novel_id=novel.id,
            logical_event_id="1:e1",
            title="开局",
            description="描述",
            event_type="plot",
            time_precision="exact" if exact_time else "unknown",
            exact_time=exact_time,
            fuzzy_start=exact_time,
            fuzzy_end=exact_time,
            narrative_chapter_number=1,
            narrative_index=0,
            story_rank=1,
            story_constraints=[],
            confidence=0.9,
            prompt_hash="c" * 64,
            schema_hash="d" * 64,
            model_lineage={"stage": "chapter_extract"},
            publication_status="provisional",
        )
    )
    await db_session.commit()
    return version.id


@pytest.mark.asyncio
async def test_manifest_json_serializable_with_datetime_columns(db_session):
    """The whole point: datetime columns must not blow up JSON serialization."""
    version_id = await _seed_scope(
        db_session, exact_time=datetime(1976, 1, 1, tzinfo=UTC)
    )
    manifest, checksum = await snapshot_manifest(db_session, version_id)

    # Must not raise TypeError
    encoded = json.dumps(manifest, sort_keys=True)
    assert checksum
    event = manifest["events"][0]
    # SQLite test backend drops tzinfo on round-trip; assert string form only
    assert event["exact_time"].startswith("1976-01-01T00:00:00")
    assert isinstance(event["fuzzy_start"], str)
    assert isinstance(event["fuzzy_end"], str)
    assert isinstance(encoded, str)


@pytest.mark.asyncio
async def test_manifest_json_serializable_without_datetimes(db_session):
    """Null datetime columns must stay null (no 'None' string leakage)."""
    version_id = await _seed_scope(db_session, exact_time=None)
    manifest, checksum = await snapshot_manifest(db_session, version_id)
    json.dumps(manifest, sort_keys=True)
    event = manifest["events"][0]
    assert event["exact_time"] is None
    assert event["fuzzy_start"] is None
    assert event["fuzzy_end"] is None
    assert checksum
