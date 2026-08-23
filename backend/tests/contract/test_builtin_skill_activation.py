"""Builtin Skill default activation contracts (TDD RED -> GREEN)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import Novel, SkillRegistry, SkillVersion, User
from app.services.agent_runtime.registry import (
    ensure_builtin_skills,
    set_skill_version_status,
    skill_runtime_manifest,
    skill_version_view_payload,
)

pytestmark = pytest.mark.contract

EXPECTED_BUILTIN_SKILLS = {
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


async def _owned_novel(db_session) -> Novel:
    user = await db_session.scalar(select(User).where(User.username == "testuser"))
    assert user is not None
    novel = Novel(owner_id=user.id, title="Builtin Skill 测试小说", status="ready")
    db_session.add(novel)
    await db_session.flush()
    return novel


async def test_builtin_activation_materializes_authoritative_runtime_contracts(
    auth_client, db_session
):
    novel = await _owned_novel(db_session)

    rows = await ensure_builtin_skills(
        db_session, owner_id=novel.owner_id, novel_id=novel.id
    )

    assert {row.name for row in rows} == EXPECTED_BUILTIN_SKILLS
    assert all(row.status == "active" for row in rows)
    assert all(row.execution_mode == "builtin" for row in rows)

    required = {
        "answer-reading-question",
        "analyze-chapter",
        "build-story-arc",
        "propose-world-model-candidates",
    }
    for row in rows:
        manifest = skill_runtime_manifest(row)
        assert manifest.execution_mode == "builtin"
        assert len(manifest.checksum) == 64
        assert manifest.input_schema
        assert manifest.output_schema
        assert manifest.allowed_tools
        if row.name in required:
            assert row.name in EXPECTED_BUILTIN_SKILLS

    builtin_payload = skill_version_view_payload(rows[0])
    assert builtin_payload["execution_status"] == "active_runtime"
    assert "Pi runtime" in builtin_payload["runtime_note"]


async def test_builtin_activation_is_idempotent_and_preserves_user_disable(
    auth_client, db_session
):
    novel = await _owned_novel(db_session)
    first = await ensure_builtin_skills(
        db_session, owner_id=novel.owner_id, novel_id=novel.id
    )
    target = next(row for row in first if row.name == "analyze-chapter")
    await set_skill_version_status(
        db_session,
        owner_id=novel.owner_id,
        skill_name=target.name,
        skill_version_id=target.id,
        status="deprecated",
    )

    second = await ensure_builtin_skills(
        db_session, owner_id=novel.owner_id, novel_id=novel.id
    )

    assert {row.id for row in second} == {row.id for row in first}
    disabled = next(row for row in second if row.name == target.name)
    assert disabled.status == "deprecated"
    assert await db_session.scalar(
        select(func.count(SkillRegistry.id)).where(
            SkillRegistry.owner_id == novel.owner_id,
            SkillRegistry.novel_id == novel.id,
        )
    ) == len(EXPECTED_BUILTIN_SKILLS)
    assert await db_session.scalar(
        select(func.count(SkillVersion.id)).where(
            SkillVersion.owner_id == novel.owner_id,
            SkillVersion.novel_id == novel.id,
        )
    ) == len(EXPECTED_BUILTIN_SKILLS)


async def test_novel_management_read_backfills_existing_novel_without_reopening_disabled(
    auth_client, db_session
):
    novel = await _owned_novel(db_session)

    response = await auth_client.get(f"/api/agent/skills?novel_id={novel.id}")
    assert response.status_code == 200, response.text
    assert {
        item["name"] for item in response.json()["items"]
    } == EXPECTED_BUILTIN_SKILLS

    target = await db_session.scalar(
        select(SkillVersion).where(
            SkillVersion.novel_id == novel.id,
            SkillVersion.name == "analyze-chapter",
        )
    )
    assert target is not None
    await set_skill_version_status(
        db_session,
        owner_id=novel.owner_id,
        skill_name=target.name,
        skill_version_id=target.id,
        status="draft",
    )

    response = await auth_client.get(f"/api/agent/skills?novel_id={novel.id}")
    assert response.status_code == 200, response.text
    disabled = next(
        item for item in response.json()["items"] if item["name"] == "analyze-chapter"
    )
    assert disabled["status"] == "draft"


async def test_create_novel_api_initializes_builtin_skills(auth_client, db_session):
    response = await auth_client.post(
        "/api/novels",
        json={"title": "新建小说的默认技能"},
    )

    assert response.status_code == 201, response.text
    novel_id = response.json()["id"]
    rows = (
        await db_session.scalars(
            select(SkillVersion).where(SkillVersion.novel_id == novel_id)
        )
    ).all()
    assert {row.name for row in rows} == EXPECTED_BUILTIN_SKILLS
