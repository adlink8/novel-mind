"""PostgreSQL API tests for Phase 10 reader-chat conversation lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.security import create_access_token, hash_password
from app.models.novel import Chapter, Novel
from app.models.reader_chat import (
    ReaderContextEvidenceRef,
    ReaderContextManifest,
    ReaderConversation,
    ReaderGenerationJob,
    ReaderMessage,
    ReaderMessageSelection,
)
from app.models.user import User

pytestmark = pytest.mark.integration

CHAPTER_CONTENT = "第一章正文：阿宁走进竹林，月光洒在青石上。"
HEX64 = hashlib.sha256(CHAPTER_CONTENT.encode("utf-8")).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"chat_owner_{suffix}",
            email=f"chat_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        other = User(
            username=f"chat_other_{suffix}",
            email=f"chat_other_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add_all([owner, other])
        session.flush()

        novel = Novel(
            title=f"Chat Novel {suffix}",
            owner_id=owner.id,
            status="ready",
            reading_progress={},
            chapter_count=1,
            word_count=len(CHAPTER_CONTENT),
        )
        other_novel = Novel(
            title=f"Other Novel {suffix}",
            owner_id=other.id,
            status="ready",
            reading_progress={},
            chapter_count=1,
            word_count=len(CHAPTER_CONTENT),
        )
        session.add_all([novel, other_novel])
        session.flush()

        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content=CHAPTER_CONTENT,
            word_count=len(CHAPTER_CONTENT),
        )
        other_chapter = Chapter(
            novel_id=other_novel.id,
            chapter_number=1,
            title="第一章",
            content=CHAPTER_CONTENT,
            word_count=len(CHAPTER_CONTENT),
        )
        session.add_all([chapter, other_chapter])
        session.commit()
        data = {
            "owner_id": owner.id,
            "other_id": other.id,
            "novel_id": novel.id,
            "other_novel_id": other_novel.id,
            "chapter_id": chapter.id,
            "other_chapter_id": other_chapter.id,
            "owner_token": create_access_token({"sub": str(owner.id)}),
            "other_token": create_access_token({"sub": str(other.id)}),
        }
    engine.dispose()
    return data


def _selection_payload(chapter_id: int, start: int = 0, end: int | None = None) -> dict[str, Any]:
    if end is None:
        end = min(8, len(CHAPTER_CONTENT))
    text_slice = CHAPTER_CONTENT[start:end]
    return {
        "chapter_id": chapter_id,
        "source_start": start,
        "source_end": end,
        "selection_text": text_slice,
        "selection_text_hash": _sha256(text_slice),
        "chapter_content_hash": HEX64,
    }


def _message_payload(
    chapter_id: int,
    *,
    client_message_id: str,
    body: str = "这段是什么意思？",
    start: int = 0,
    end: int | None = None,
) -> dict[str, Any]:
    return {
        "client_message_id": client_message_id,
        "body": body,
        "selection": _selection_payload(chapter_id, start=start, end=end),
    }


@pytest.mark.asyncio
async def test_conversation_lifecycle_create_list_rename_archive_restore_delete(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner_novel(sync_url, suffix=f"life_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['owner_token']}"}
    base = f"/api/novels/{ids['novel_id']}/conversations"

    c1 = await client.post(base, json={"title": "选区讨论 A"}, headers=headers)
    assert c1.status_code == 201, c1.text
    conv1 = c1.json()
    assert conv1["title"] == "选区讨论 A"
    assert conv1["status"] == "active"
    assert conv1["next_sequence"] == 1
    assert "body" not in conv1

    c2 = await client.post(base, json={"title": "选区讨论 B"}, headers=headers)
    assert c2.status_code == 201
    conv2 = c2.json()

    listed = await client.get(base, headers=headers)
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 2
    assert len(payload["items"]) == 2
    for item in payload["items"]:
        assert "body" not in item
        assert "excerpt" not in item
        assert "selection_text" not in item
        assert set(item.keys()) >= {
            "id",
            "novel_id",
            "title",
            "status",
            "next_sequence",
            "created_at",
            "updated_at",
        }

    renamed = await client.patch(
        f"{base}/{conv1['id']}",
        json={"title": "重命名会话"},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "重命名会话"

    archived = await client.patch(
        f"{base}/{conv1['id']}",
        json={"status": "archived"},
        headers=headers,
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    detail = await client.get(f"{base}/{conv1['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "archived"

    blocked = await client.post(
        f"{base}/{conv1['id']}/messages",
        json=_message_payload(ids["chapter_id"], client_message_id="blocked-1"),
        headers=headers,
    )
    assert blocked.status_code == 409

    restored = await client.patch(
        f"{base}/{conv1['id']}",
        json={"status": "active"},
        headers=headers,
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"

    accepted = await client.post(
        f"{base}/{conv1['id']}/messages",
        json=_message_payload(ids["chapter_id"], client_message_id="ok-1"),
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["message"]["role"] == "user"
    assert body["message"]["sequence"] == 1
    assert body["message"]["client_message_id"] == "ok-1"
    assert body["message"]["selection"] is not None
    assert body["job"]["status"] == "queued"
    assert body["job"]["user_message_id"] == body["message"]["id"]

    listed2 = await client.get(base, headers=headers)
    assert listed2.status_code == 200
    for item in listed2.json()["items"]:
        assert "body" not in item
        if item["id"] == conv1["id"]:
            assert item["last_message_sequence"] == 1
            assert item["last_message_role"] == "user"

    deleted = await client.delete(f"{base}/{conv1['id']}", headers=headers)
    assert deleted.status_code == 204

    gone = await client.get(f"{base}/{conv1['id']}", headers=headers)
    assert gone.status_code == 404

    still = await client.get(f"{base}/{conv2['id']}", headers=headers)
    assert still.status_code == 200

    async with factory() as session:
        msgs = (
            await session.execute(
                select(ReaderMessage).where(
                    ReaderMessage.conversation_id == conv1["id"]
                )
            )
        ).scalars().all()
        assert msgs == []
        jobs = (
            await session.execute(
                select(ReaderGenerationJob).where(
                    ReaderGenerationJob.conversation_id == conv1["id"]
                )
            )
        ).scalars().all()
        assert jobs == []
        sel = (
            await session.execute(
                select(ReaderMessageSelection).where(
                    ReaderMessageSelection.conversation_id == conv1["id"]
                )
            )
        ).scalars().all()
        assert sel == []
        manifests = (
            await session.execute(
                select(ReaderContextManifest).where(
                    ReaderContextManifest.conversation_id == conv1["id"]
                )
            )
        ).scalars().all()
        assert manifests == []


@pytest.mark.asyncio
async def test_message_idempotency_replay_and_pagination(api_client):
    client, _factory, sync_url = api_client
    ids = _seed_owner_novel(sync_url, suffix=f"idem_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['owner_token']}"}
    base = f"/api/novels/{ids['novel_id']}/conversations"
    conv = (await client.post(base, json={"title": "幂等"}, headers=headers)).json()
    conv_id = conv["id"]
    msg_url = f"{base}/{conv_id}/messages"

    payload = _message_payload(
        ids["chapter_id"], client_message_id="dup-client-1", body="第一次"
    )
    first = await client.post(msg_url, json=payload, headers=headers)
    assert first.status_code == 202, first.text
    first_body = first.json()
    msg_id = first_body["message"]["id"]
    job_id = first_body["job"]["id"]
    seq = first_body["message"]["sequence"]

    retry = await client.post(msg_url, json=payload, headers=headers)
    assert retry.status_code == 202
    retry_body = retry.json()
    assert retry_body["message"]["id"] == msg_id
    assert retry_body["job"]["id"] == job_id
    assert retry_body["message"]["sequence"] == seq

    second = await client.post(
        msg_url,
        json=_message_payload(
            ids["chapter_id"],
            client_message_id="dup-client-2",
            body="第二次",
            start=2,
            end=10,
        ),
        headers=headers,
    )
    assert second.status_code == 202
    assert second.json()["message"]["sequence"] == seq + 1

    all_msgs = await client.get(msg_url, headers=headers)
    assert all_msgs.status_code == 200
    all_payload = all_msgs.json()
    assert all_payload["total"] == 2
    assert [m["sequence"] for m in all_payload["items"]] == [1, 2]
    assert all(m.get("selection") is not None for m in all_payload["items"])
    assert all(m["generation_job"] is not None for m in all_payload["items"])

    after = await client.get(f"{msg_url}?after_sequence=1", headers=headers)
    assert after.status_code == 200
    after_items = after.json()["items"]
    assert len(after_items) == 1
    assert after_items[0]["sequence"] == 2

    job = await client.get(f"{base}/{conv_id}/jobs/{job_id}", headers=headers)
    assert job.status_code == 200
    assert job.json()["status"] == "queued"

    cancelled = await client.post(
        f"{base}/{conv_id}/jobs/{job_id}/cancel", headers=headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["cancel_requested"] is True
    assert cancelled.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_delete_cancels_active_jobs_then_removes_graph(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner_novel(sync_url, suffix=f"deljob_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['owner_token']}"}
    base = f"/api/novels/{ids['novel_id']}/conversations"
    conv = (await client.post(base, json={"title": "待删"}, headers=headers)).json()
    accepted = await client.post(
        f"{base}/{conv['id']}/messages",
        json=_message_payload(ids["chapter_id"], client_message_id="to-delete"),
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job"]["id"]
    msg_id = accepted.json()["message"]["id"]

    async with factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        assert job is not None
        job.status = "running"
        await session.commit()

    deleted = await client.delete(f"{base}/{conv['id']}", headers=headers)
    assert deleted.status_code == 204

    async with factory() as session:
        assert await session.get(ReaderConversation, conv["id"]) is None
        assert await session.get(ReaderMessage, msg_id) is None
        assert await session.get(ReaderGenerationJob, job_id) is None
        leftovers = (
            await session.execute(
                select(ReaderContextEvidenceRef)
                .join(
                    ReaderContextManifest,
                    ReaderContextEvidenceRef.manifest_id == ReaderContextManifest.id,
                )
                .where(ReaderContextManifest.conversation_id == conv["id"])
            )
        ).scalars().all()
        assert leftovers == []


@pytest.mark.asyncio
async def test_concurrent_appends_receive_unique_monotonic_sequences(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner_novel(sync_url, suffix=f"conc_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['owner_token']}"}
    base = f"/api/novels/{ids['novel_id']}/conversations"
    conv = (await client.post(base, json={"title": "并发"}, headers=headers)).json()
    conv_id = conv["id"]
    msg_url = f"{base}/{conv_id}/messages"

    async def _send(i: int):
        return await client.post(
            msg_url,
            json=_message_payload(
                ids["chapter_id"],
                client_message_id=f"concurrent-{i}",
                body=f"并发消息 {i}",
                start=0,
                end=4 + (i % 3),
            ),
            headers=headers,
        )

    results = await asyncio.gather(*[_send(i) for i in range(5)])
    assert all(r.status_code == 202 for r in results), [r.text for r in results]
    sequences = sorted(r.json()["message"]["sequence"] for r in results)
    assert sequences == [1, 2, 3, 4, 5]
    message_ids = {r.json()["message"]["id"] for r in results}
    job_ids = {r.json()["job"]["id"] for r in results}
    assert len(message_ids) == 5
    assert len(job_ids) == 5

    async with factory() as session:
        rows = (
            await session.execute(
                select(ReaderMessage.sequence)
                .where(ReaderMessage.conversation_id == conv_id)
                .order_by(ReaderMessage.sequence)
            )
        ).all()
        assert [r[0] for r in rows] == [1, 2, 3, 4, 5]
        conv_row = await session.get(ReaderConversation, conv_id)
        assert conv_row is not None
        assert conv_row.next_sequence == 6


@pytest.mark.asyncio
async def test_rename_validation_and_list_pagination(api_client):
    client, _factory, sync_url = api_client
    ids = _seed_owner_novel(sync_url, suffix=f"page_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['owner_token']}"}
    base = f"/api/novels/{ids['novel_id']}/conversations"

    for i in range(3):
        resp = await client.post(base, json={"title": f"会话 {i}"}, headers=headers)
        assert resp.status_code == 201

    page = await client.get(f"{base}?skip=1&limit=1", headers=headers)
    assert page.status_code == 200
    data = page.json()
    assert data["total"] == 3
    assert data["skip"] == 1
    assert data["limit"] == 1
    assert len(data["items"]) == 1

    bad = await client.patch(
        f"{base}/{data['items'][0]['id']}",
        json={},
        headers=headers,
    )
    assert bad.status_code == 422

    empty_title = await client.patch(
        f"{base}/{data['items'][0]['id']}",
        json={"title": ""},
        headers=headers,
    )
    assert empty_title.status_code == 422


@pytest.mark.asyncio
async def test_message_requires_committed_manifest_graph(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner_novel(sync_url, suffix=f"manifest_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['owner_token']}"}
    base = f"/api/novels/{ids['novel_id']}/conversations"
    conv = (await client.post(base, json={"title": "清单"}, headers=headers)).json()
    accepted = await client.post(
        f"{base}/{conv['id']}/messages",
        json=_message_payload(ids["chapter_id"], client_message_id="manifest-1"),
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    msg_id = accepted.json()["message"]["id"]

    async with factory() as session:
        selection = (
            await session.execute(
                select(ReaderMessageSelection).where(
                    ReaderMessageSelection.user_message_id == msg_id
                )
            )
        ).scalar_one()
        manifest = (
            await session.execute(
                select(ReaderContextManifest).where(
                    ReaderContextManifest.user_message_id == msg_id
                )
            )
        ).scalar_one()
        refs = (
            await session.execute(
                select(ReaderContextEvidenceRef).where(
                    ReaderContextEvidenceRef.manifest_id == manifest.id
                )
            )
        ).scalars().all()
        assert selection.chapter_id == ids["chapter_id"]
        assert selection.selection_text_hash
        assert manifest.manifest_checksum
        assert len(refs) >= 1
        assert any(r.source_type == "selection" for r in refs)
        job = (
            await session.execute(
                select(ReaderGenerationJob).where(
                    ReaderGenerationJob.user_message_id == msg_id
                )
            )
        ).scalar_one()
        assert job.status == "queued"
        assert job.context_manifest_checksum == manifest.manifest_checksum


@pytest.mark.asyncio
async def test_wrong_novel_child_ids_are_404(api_client):
    client, _factory, sync_url = api_client
    ids = _seed_owner_novel(sync_url, suffix=f"wrongn_{uuid.uuid4().hex[:8]}")
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        second = Novel(
            title="Second owned novel",
            owner_id=ids["owner_id"],
            status="ready",
            reading_progress={},
        )
        session.add(second)
        session.flush()
        second_id = second.id
        session.commit()
    engine.dispose()

    headers = {"Authorization": f"Bearer {ids['owner_token']}"}
    created = await client.post(
        f"/api/novels/{ids['novel_id']}/conversations",
        json={"title": "A"},
        headers=headers,
    )
    conv_id = created.json()["id"]
    msg = await client.post(
        f"/api/novels/{ids['novel_id']}/conversations/{conv_id}/messages",
        json=_message_payload(ids["chapter_id"], client_message_id="wn-1"),
        headers=headers,
    )
    assert msg.status_code == 202, msg.text
    job_id = msg.json()["job"]["id"]

    wrong_base = f"/api/novels/{second_id}/conversations"
    for path in (
        f"{wrong_base}/{conv_id}",
        f"{wrong_base}/{conv_id}/messages",
        f"{wrong_base}/{conv_id}/jobs/{job_id}",
    ):
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 404, path
        assert "owner" not in resp.text.lower()
