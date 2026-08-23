from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import Novel, SkillRegistry, SkillVersion, User
from app.services.novel_service import novel_service
from app.services.agent_runtime.builtin_manifests import builtin_skill_manifests
from app.services.agent_runtime.registry import (
    ensure_builtin_skills,
    set_skill_version_status,
)


pytestmark = pytest.mark.unit


async def _novel(db_session) -> Novel:
    user = User(
        username="builtin_skill_owner",
        email="builtin_skill_owner@example.com",
        hashed_password="not-a-real-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    novel = Novel(owner_id=user.id, title="Builtin skill test", status="ready")
    db_session.add(novel)
    await db_session.flush()
    return novel


def test_builtin_catalog_uses_all_allowlisted_skill_assets() -> None:
    contracts = builtin_skill_manifests()

    assert len(contracts) == 15
    assert {contract["name"] for contract in contracts} == {
        "answer-reading-question",
        "propose-world-model-candidates",
        "analyze-chapter",
        "build-story-arc",
        "evaluate-reading-skill-runs",
        "build-visual-bible",
        "detect-key-scenes",
        "compile-scene-spec",
        "illustrate-scene",
        "propose-illustration-anchor",
        "create-canon-fork",
        "edit-derivative-story",
        "continue-derivative-story",
        "illustrate-derivative-scene",
        "prepare-export",
    }
    answer = next(
        item for item in contracts if item["name"] == "answer-reading-question"
    )
    assert answer["version"] == "1.0.0"
    assert answer["allowed_tools"] == [
        "get_novel",
        "get_chapter",
        "search_novel_text",
        "get_timeline",
        "get_relationships",
        "get_clues",
    ]
    assert answer["input_schema"]["required"] == ["question", "novel_id"]


@pytest.mark.asyncio
async def test_ensure_builtin_skills_is_idempotent(db_session) -> None:
    novel = await _novel(db_session)

    first = await ensure_builtin_skills(
        db_session, owner_id=novel.owner_id, novel_id=novel.id
    )
    second = await ensure_builtin_skills(
        db_session, owner_id=novel.owner_id, novel_id=novel.id
    )

    assert len(first) == len(second) == 15
    registry_count = await db_session.scalar(
        select(func.count()).where(SkillRegistry.novel_id == novel.id)
    )
    version_count = await db_session.scalar(
        select(func.count()).where(SkillVersion.novel_id == novel.id)
    )
    assert registry_count == version_count == 15
    assert all(version.status == "active" for version in second)


@pytest.mark.asyncio
async def test_ensure_builtin_skills_does_not_reopen_disabled_skill(db_session) -> None:
    novel = await _novel(db_session)
    created = await ensure_builtin_skills(
        db_session, owner_id=novel.owner_id, novel_id=novel.id
    )
    disabled_version = next(
        version for version in created if version.name == "answer-reading-question"
    )
    await set_skill_version_status(
        db_session,
        owner_id=novel.owner_id,
        skill_name=disabled_version.name,
        skill_version_id=disabled_version.id,
        status="deprecated",
    )

    await ensure_builtin_skills(db_session, owner_id=novel.owner_id, novel_id=novel.id)

    assert disabled_version.status == "deprecated"
    versions = (
        await db_session.scalars(
            select(SkillVersion).where(
                SkillVersion.registry_id == disabled_version.registry_id
            )
        )
    ).all()
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_novel_creation_initializes_builtin_skills(db_session) -> None:
    user = User(
        username="builtin_creation_owner",
        email="builtin_creation_owner@example.com",
        hashed_password="not-a-real-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    novel = await novel_service.create_novel_record(
        db_session,
        "Created with defaults",
        [{"chapter_number": 1, "title": "第一章", "content": "正文", "word_count": 2}],
        owner_id=user.id,
    )

    count = await db_session.scalar(
        select(func.count()).where(SkillRegistry.novel_id == novel.id)
    )
    assert count == 15


@pytest.mark.asyncio
async def test_novel_management_read_backfills_existing_novel(
    auth_client, db_session
) -> None:
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    novel = Novel(owner_id=owner.id, title="Existing without defaults", status="ready")
    db_session.add(novel)
    await db_session.flush()

    response = await auth_client.get(f"/api/novels/{novel.id}")

    assert response.status_code == 200
    count = await db_session.scalar(
        select(func.count()).where(SkillRegistry.novel_id == novel.id)
    )
    assert count == 15

    skills_response = await auth_client.get(
        "/api/agent/skills", params={"novel_id": novel.id}
    )
    assert skills_response.status_code == 200
    assert skills_response.json()["total"] == 15

    versions_response = await auth_client.get(
        "/api/agent/skills/answer-reading-question/versions",
        params={"novel_id": novel.id},
    )
    assert versions_response.status_code == 200
    version = versions_response.json()["items"][0]
    assert version["runtime_manifest"]["input_schema"]["required"] == [
        "question",
        "novel_id",
    ]
    assert version["runtime_manifest"]["prompt"]
    assert version["runtime_manifest"]["checksum"] == version["yaml_checksum"]
