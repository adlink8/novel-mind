"""Cross-owner IDOR matrix for reader-chat conversation resources."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.security import create_access_token, hash_password
from app.models.novel import Chapter, Novel
from app.models.user import User

pytestmark = pytest.mark.integration

CHAPTER_CONTENT = "隔离测试正文内容，足够选取一段。"
HEX64 = hashlib.sha256(CHAPTER_CONTENT.encode("utf-8")).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_two_owners(sync_url: str, *, suffix: str) -> dict[str, Any]:
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner_a = User(
            username=f"iso_a_{suffix}",
            email=f"iso_a_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        owner_b = User(
            username=f"iso_b_{suffix}",
            email=f"iso_b_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add_all([owner_a, owner_b])
        session.flush()

        novel_a = Novel(
            title=f"Novel A {suffix}",
            owner_id=owner_a.id,
            status="ready",
            reading_progress={},
            chapter_count=1,
            word_count=len(CHAPTER_CONTENT),
        )
        novel_b = Novel(
            title=f"Novel B {suffix}",
            owner_id=owner_b.id,
            status="ready",
            reading_progress={},
            chapter_count=1,
            word_count=len(CHAPTER_CONTENT),
        )
        session.add_all([novel_a, novel_b])
        session.flush()

        ch_a = Chapter(
            novel_id=novel_a.id,
            chapter_number=1,
            title="A1",
            content=CHAPTER_CONTENT,
            word_count=len(CHAPTER_CONTENT),
        )
        ch_b = Chapter(
            novel_id=novel_b.id,
            chapter_number=1,
            title="B1",
            content=CHAPTER_CONTENT,
            word_count=len(CHAPTER_CONTENT),
        )
        session.add_all([ch_a, ch_b])
        session.commit()
        data = {
            "a_id": owner_a.id,
            "b_id": owner_b.id,
            "novel_a": novel_a.id,
            "novel_b": novel_b.id,
            "chapter_a": ch_a.id,
            "chapter_b": ch_b.id,
            "token_a": create_access_token({"sub": str(owner_a.id)}),
            "token_b": create_access_token({"sub": str(owner_b.id)}),
        }
    engine.dispose()
    return data


def _message_payload(chapter_id: int, client_message_id: str) -> dict[str, Any]:
    start, end = 0, 6
    text_slice = CHAPTER_CONTENT[start:end]
    return {
        "client_message_id": client_message_id,
        "body": "跨用户隔离？",
        "selection": {
            "chapter_id": chapter_id,
            "source_start": start,
            "source_end": end,
            "selection_text": text_slice,
            "selection_text_hash": _sha256(text_slice),
            "chapter_content_hash": HEX64,
        },
    }


@pytest.mark.asyncio
async def test_cross_owner_novel_conversation_message_job_matrix_404(api_client):
    client, _factory, sync_url = api_client
    ids = _seed_two_owners(sync_url, suffix=f"matrix_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {ids['token_a']}"}
    headers_b = {"Authorization": f"Bearer {ids['token_b']}"}

    base_a = f"/api/novels/{ids['novel_a']}/conversations"
    conv = (
        await client.post(base_a, json={"title": "私有会话"}, headers=headers_a)
    ).json()
    msg = await client.post(
        f"{base_a}/{conv['id']}/messages",
        json=_message_payload(ids["chapter_a"], "iso-msg-1"),
        headers=headers_a,
    )
    assert msg.status_code == 202, msg.text
    job_id = msg.json()["job"]["id"]
    message_id = msg.json()["message"]["id"]

    foreign_novel = await client.get(
        f"/api/novels/{ids['novel_a']}/conversations",
        headers=headers_b,
    )
    assert foreign_novel.status_code == 404
    missing_novel = await client.get(
        "/api/novels/999999991/conversations",
        headers=headers_b,
    )
    assert missing_novel.status_code == 404
    assert foreign_novel.json() == missing_novel.json()

    base_b = f"/api/novels/{ids['novel_b']}/conversations"
    paths = [
        ("GET", f"{base_b}/{conv['id']}"),
        ("GET", f"{base_b}/{conv['id']}/messages"),
        ("GET", f"{base_b}/{conv['id']}/jobs/{job_id}"),
        ("POST", f"{base_b}/{conv['id']}/jobs/{job_id}/cancel"),
        ("POST", f"{base_b}/{conv['id']}/jobs/{job_id}/retry"),
        ("PATCH", f"{base_b}/{conv['id']}"),
        ("DELETE", f"{base_b}/{conv['id']}"),
        ("POST", f"{base_b}/{conv['id']}/messages"),
    ]
    for method, path in paths:
        if method == "GET":
            resp = await client.get(path, headers=headers_b)
        elif method == "POST" and path.endswith("/messages"):
            resp = await client.post(
                path,
                json=_message_payload(ids["chapter_b"], "steal"),
                headers=headers_b,
            )
        elif method == "POST":
            resp = await client.post(path, headers=headers_b)
        elif method == "PATCH":
            resp = await client.patch(
                path, json={"title": "hijack"}, headers=headers_b
            )
        else:
            resp = await client.delete(path, headers=headers_b)
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"
        assert "owner" not in resp.text.lower()
        assert str(message_id) not in resp.text

    steal_detail = await client.get(f"{base_a}/{conv['id']}", headers=headers_b)
    assert steal_detail.status_code == 404
    steal_job = await client.get(
        f"{base_a}/{conv['id']}/jobs/{job_id}", headers=headers_b
    )
    assert steal_job.status_code == 404

    ok = await client.get(f"{base_a}/{conv['id']}", headers=headers_a)
    assert ok.status_code == 200
    assert ok.json()["id"] == conv["id"]


@pytest.mark.asyncio
async def test_unauthenticated_conversation_routes_reject(api_client):
    client, _factory, sync_url = api_client
    ids = _seed_two_owners(sync_url, suffix=f"anon_{uuid.uuid4().hex[:8]}")
    base = f"/api/novels/{ids['novel_a']}/conversations"
    resp = await client.get(base)
    assert resp.status_code == 401
    resp2 = await client.post(base, json={"title": "x"})
    assert resp2.status_code == 401
