"""Unit tests for app.api.dependencies.require_owned_novel."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

from app.api.dependencies import require_owned_novel
from app.models import Novel, User


async def _seed(db, owner_id: int) -> Novel:
    novel = Novel(title="依赖测试", owner_id=owner_id, status="ready")
    db.add(novel)
    await db.flush()
    return novel


class _Actor:
    def __init__(self, id: int, is_superuser: bool = False):
        self.id = id
        self.is_superuser = is_superuser


async def test_owner_match_returns_novel(db_session):
    user = User(
        username="ownermatch",
        email="ownermatch@example.com",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    novel = await _seed(db_session, user.id)

    result = await require_owned_novel(novel.id, db_session, _Actor(user.id))
    assert result.id == novel.id


async def test_owner_mismatch_non_superuser_404(db_session):
    user = User(
        username="owner1",
        email="owner1@example.com",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    novel = await _seed(db_session, user.id)

    with pytest.raises(HTTPException) as exc:
        await require_owned_novel(novel.id, db_session, _Actor(user.id + 1))
    assert exc.value.status_code == 404


async def test_superuser_cross_owner_passes(db_session):
    user = User(
        username="owner2",
        email="owner2@example.com",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    novel = await _seed(db_session, user.id)

    result = await require_owned_novel(
        novel.id, db_session, _Actor(999, is_superuser=True)
    )
    assert result.id == novel.id


async def test_novel_not_found_404(db_session):
    user = User(
        username="owner3",
        email="owner3@example.com",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    with pytest.raises(HTTPException) as exc:
        await require_owned_novel(424242, db_session, _Actor(user.id))
    assert exc.value.status_code == 404
