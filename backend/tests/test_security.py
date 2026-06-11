"""Security regression tests for authentication and resource isolation."""

import io
import os
import socket
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from pydantic import ValidationError

from app.config import Settings
from app.core.crypto import decrypt_text, encrypt_text
from app.core.url_security import validate_ai_base_url
from app.models import Novel, User
from app.services.novel_service import novel_service


async def _register_and_login(client: AsyncClient, username: str) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "correct-horse-123",
        },
    )
    assert response.status_code == 201
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "correct-horse-123"},
    )
    assert response.status_code == 200
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.mark.asyncio
async def test_protected_apis_reject_anonymous_requests(client: AsyncClient):
    for method, path in [
        ("GET", "/api/novels"),
        ("GET", "/api/models"),
        ("GET", "/api/analysis/1"),
        ("GET", "/api/timeline/1"),
        ("GET", "/api/characters/1"),
        ("GET", "/api/fanfiction/1"),
    ]:
        response = await client.request(method, path)
        assert response.status_code == 401, path


@pytest.mark.asyncio
async def test_cookie_authenticated_writes_require_allowed_origin(client: AsyncClient):
    await _register_and_login(client, "cookieowner")
    client.headers.pop("Authorization")

    rejected = await client.post(
        "/api/novels/upload",
        files={
            "file": ("blocked.txt", io.BytesIO(b"Chapter 1\ncontent"), "text/plain")
        },
    )
    assert rejected.status_code == 403

    accepted = await client.post(
        "/api/novels/upload",
        headers={"Origin": "http://localhost:3000"},
        files={
            "file": ("allowed.txt", io.BytesIO(b"Chapter 1\ncontent"), "text/plain")
        },
    )
    assert accepted.status_code == 200


@pytest.mark.asyncio
async def test_registration_rejects_password_over_bcrypt_byte_limit(
    client: AsyncClient,
):
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "longpassword",
            "email": "longpassword@example.com",
            "password": "密" * 25,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_novel_endpoints_hide_other_users_resources(client: AsyncClient):
    await _register_and_login(client, "owneruser")
    upload = await client.post(
        "/api/novels/upload",
        files={
            "file": ("private.txt", io.BytesIO("第一章\nsecret".encode()), "text/plain")
        },
    )
    assert upload.status_code == 200
    novel_id = upload.json()["id"]
    chapter_id = (await client.get(f"/api/novels/{novel_id}/chapters")).json()[0]["id"]

    await _register_and_login(client, "otheruser")
    assert (await client.get("/api/novels")).json()["items"] == []
    for method, path, payload in [
        ("GET", f"/api/novels/{novel_id}", None),
        ("GET", f"/api/novels/{novel_id}/chapters", None),
        ("GET", f"/api/novels/{novel_id}/chapters/{chapter_id}", None),
        ("GET", f"/api/novels/{novel_id}/import-status", None),
        (
            "PATCH",
            f"/api/novels/{novel_id}/progress",
            {"chapter_id": chapter_id, "progress_percent": 10},
        ),
        ("DELETE", f"/api/novels/{novel_id}", None),
    ]:
        response = await client.request(method, path, json=payload)
        assert response.status_code == 404, path


@pytest.mark.asyncio
async def test_model_configs_are_isolated_per_user(client: AsyncClient):
    await _register_and_login(client, "modelowner")
    created = await client.post(
        "/api/models",
        json={"name": "private-model", "provider": "openai", "model_id": "gpt-4o"},
    )
    assert created.status_code == 200
    model_id = created.json()["id"]

    await _register_and_login(client, "modelother")
    assert (await client.get("/api/models")).json() == []
    for method, path in [
        ("PUT", f"/api/models/{model_id}"),
        ("POST", f"/api/models/{model_id}/test"),
        ("POST", f"/api/models/{model_id}/default"),
        ("DELETE", f"/api/models/{model_id}"),
    ]:
        payload = {"temperature": 0.2} if method == "PUT" else None
        response = await client.request(method, path, json=payload)
        assert response.status_code == 404, path


@pytest.mark.asyncio
async def test_ssrf_rejects_unlisted_and_private_dns(monkeypatch):
    with pytest.raises(HTTPException):
        await validate_ai_base_url("https://attacker.example/v1")

    monkeypatch.setattr(
        "app.core.url_security.settings.ai_allowed_hosts", "safe.example"
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    with pytest.raises(HTTPException):
        await validate_ai_base_url("https://safe.example/v1")


def test_crypto_roundtrip_and_legacy_plaintext_compatibility():
    encrypted = encrypt_text("sk-secret")
    assert encrypted and encrypted.startswith("enc:v1:")
    assert decrypt_text(encrypted) == "sk-secret"
    assert decrypt_text("legacy-plaintext-key") == "legacy-plaintext-key"


def test_production_rejects_default_or_shared_secrets():
    with pytest.raises(ValidationError):
        Settings(debug=False)
    with pytest.raises(ValidationError):
        Settings(debug=False, secret_key="x" * 40, encryption_key="x" * 40)


@pytest.mark.asyncio
async def test_delete_restores_file_when_database_commit_fails(
    db_session, monkeypatch, tmp_path
):
    user = User(
        username="rollbackuser", email="rollback@example.com", hashed_password="unused"
    )
    db_session.add(user)
    await db_session.flush()
    source = tmp_path / "rollback.txt"
    source.write_text("content", encoding="utf-8")
    novel = Novel(
        title="rollback", owner_id=user.id, source_path=str(source), status="ready"
    )
    db_session.add(novel)
    await db_session.commit()
    await db_session.refresh(novel)

    monkeypatch.setattr("app.services.novel_service.settings.upload_dir", str(tmp_path))
    monkeypatch.setattr(
        db_session, "commit", AsyncMock(side_effect=RuntimeError("commit failed"))
    )
    with pytest.raises(RuntimeError):
        await novel_service.delete_novel(db_session, novel.id)
    assert source.exists()


@pytest.mark.asyncio
async def test_upload_removes_file_when_database_write_fails(auth_client, monkeypatch):
    before = set(os.listdir("uploads")) if os.path.isdir("uploads") else set()
    monkeypatch.setattr(
        novel_service,
        "create_novel_record",
        AsyncMock(side_effect=RuntimeError("database failed")),
    )
    with pytest.raises(RuntimeError):
        await auth_client.post(
            "/api/novels/upload",
            files={
                "file": (
                    "rollback.txt",
                    io.BytesIO(b"Chapter 1\ncontent"),
                    "text/plain",
                )
            },
        )
    after = set(os.listdir("uploads")) if os.path.isdir("uploads") else set()
    assert after == before
