"""Skills/Tools 管理公共 API 契约（TDD RED -> GREEN）。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Novel, User

pytestmark = pytest.mark.contract

EXPECTED_TOOLS = {
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
    "get_narrative_memory",
    "get_events",
    "get_character_state",
    "get_character_knowledge",
    "get_world_rules",
    "get_evidence_span",
    "get_visual_bible",
    "generate_image_candidate",
    "publish_illustration",
    "attach_illustration_to_text",
    "create_canon_fork",
    "apply_derivative_edit",
    "allow_divergence",
    "publish_derivative_revision",
    "publish_derivative_visual",
    "approve_export",
    "materialize_export",
}


async def _owned_novel(db_session) -> Novel:
    user = await db_session.scalar(select(User).where(User.username == "testuser"))
    assert user is not None
    novel = Novel(owner_id=user.id, title="Skill 管理测试小说", status="ready")
    db_session.add(novel)
    await db_session.flush()
    return novel


async def test_tool_catalog_is_authenticated_and_contains_exact_capabilities(
    auth_client,
):
    response = await auth_client.get("/api/agent/tools/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert {item["name"] for item in payload["items"]} == EXPECTED_TOOLS
    assert len(payload["items"]) == 23
    assert {item["category"] for item in payload["items"]} == {
        "read",
        "candidate",
        "action",
    }
    assert all(
        set(item) >= {"name", "category", "approval_required", "user_configurable"}
        for item in payload["items"]
    )
    assert all(
        "shell" not in item["name"] and "code" not in item["name"]
        for item in payload["items"]
    )


async def test_declarative_skill_can_register_from_catalog_and_change_status(
    auth_client, db_session
):
    novel = await _owned_novel(db_session)
    catalog = await auth_client.get("/api/agent/tools/catalog")
    tool_name = catalog.json()["items"][0]["name"]
    contract = {
        "novel_id": novel.id,
        "name": "chapter-notes",
        "version": "1.0.0",
        "description": "把章节要点整理成声明式笔记。",
        "prompt": "请根据输入章节整理要点，不得补写原文未提供的事实。",
        "input_schema": {
            "type": "object",
            "properties": {"chapter_id": {"type": "integer"}},
        },
        "output_schema": {"type": "object", "properties": {"notes": {"type": "array"}}},
        "allowed_tools": [tool_name],
        "budget": {"max_tool_calls": 3, "max_tokens": 800},
    }

    created = await auth_client.post("/api/agent/skills", json=contract)

    assert created.status_code == 201
    body = created.json()
    assert body["prompt"] == contract["prompt"]
    assert body["execution_status"] == "declarative_only"
    assert "不会执行 prompt 正文" in body["runtime_note"]
    assert body["allowed_tools"] == [tool_name]
    assert body["status"] == "active"

    listed = await auth_client.get("/api/agent/skills")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["name"] == "chapter-notes"

    versions = await auth_client.get("/api/agent/skills/chapter-notes/versions")
    assert versions.status_code == 200
    assert versions.json()["items"][0]["prompt"] == contract["prompt"]

    changed = await auth_client.patch(
        f"/api/agent/skills/chapter-notes/versions/{body['id']}",
        json={"status": "draft"},
    )
    assert changed.status_code == 200
    assert changed.json()["status"] == "draft"


async def test_skill_registration_rejects_tools_outside_catalog(
    auth_client, db_session
):
    novel = await _owned_novel(db_session)
    response = await auth_client.post(
        "/api/agent/skills",
        json={
            "novel_id": novel.id,
            "name": "unsafe",
            "version": "1.0.0",
            "prompt": "run shell",
            "allowed_tools": ["shell.exec"],
            "input_schema": {},
            "output_schema": {},
            "budget": {},
        },
    )

    assert response.status_code == 400
    assert "catalog" in response.json()["detail"].lower()


async def test_skill_can_register_connector_and_run_accept_freezes_active_version(
    auth_client, db_session
):
    novel = await _owned_novel(db_session)
    connector = await auth_client.post(
        "/api/extensions/tools",
        json={
            "name": "weather_lookup",
            "base_url": "https://api.example.com",
            "path": "/v1/weather",
            "method": "GET",
            "request_schema": {"type": "object", "additionalProperties": False},
            "response_schema": {"type": "object"},
        },
    )
    assert connector.status_code == 201
    connector_id = connector.json()["id"]
    assert (
        await auth_client.post(f"/api/extensions/tools/{connector_id}/validate")
    ).status_code == 200
    assert (
        await auth_client.patch(
            f"/api/extensions/tools/{connector_id}/status", json={"status": "active"}
        )
    ).status_code == 200

    skill = await auth_client.post(
        "/api/agent/skills",
        json={
            "novel_id": novel.id,
            "name": "weather-skill",
            "version": "1.0.0",
            "prompt": "调用受限天气工具。",
            "allowed_tools": ["connector:weather_lookup"],
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
    )
    assert skill.status_code == 201, skill.text

    accepted = await auth_client.post(
        f"/api/agent/novels/{novel.id}/skill-runs",
        json={"skill_version_id": skill.json()["id"], "input": {}},
    )
    assert accepted.status_code == 202, accepted.text
    frozen = accepted.json()["runtime_manifest"]["connector_versions"]
    assert frozen[0]["tool_name"] == "connector:weather_lookup"
    assert frozen[0]["connector_id"] == connector_id
    assert frozen[0]["version_id"] == connector.json()["version_id"]
    assert frozen[0]["version"] == 1
    assert len(frozen[0]["checksum"]) == 64
    assert frozen[0]["method"] == "GET"

    proxied = await auth_client.post(
        f"/api/agent-tools/connectors/weather_lookup?novel_id={novel.id}",
        headers={"Authorization": f"Bearer {accepted.json()['internal_token']}"},
        json={},
    )
    assert proxied.status_code == 200, proxied.text
    assert proxied.json()["body"] == {}

    disabled = await auth_client.patch(
        f"/api/extensions/tools/{connector_id}/status", json={"status": "disabled"}
    )
    assert disabled.status_code == 200
    blocked = await auth_client.post(
        f"/api/agent-tools/connectors/weather_lookup?novel_id={novel.id}",
        headers={"Authorization": f"Bearer {accepted.json()['internal_token']}"},
        json={},
    )
    assert blocked.status_code == 409


async def test_skill_run_rejects_missing_or_disabled_connector(auth_client, db_session):
    novel = await _owned_novel(db_session)
    skill = await auth_client.post(
        "/api/agent/skills",
        json={
            "novel_id": novel.id,
            "name": "missing-connector-skill",
            "version": "1.0.0",
            "prompt": "调用不存在的连接器。",
            "allowed_tools": ["connector:missing"],
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
    )
    assert skill.status_code == 201, skill.text

    accepted = await auth_client.post(
        f"/api/agent/novels/{novel.id}/skill-runs",
        json={"skill_version_id": skill.json()["id"], "input": {}},
    )
    assert accepted.status_code == 409
