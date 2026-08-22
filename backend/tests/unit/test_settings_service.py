"""app_settings key-value service (routing preference read/upsert)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.app_setting import AppSetting
from app.services.settings_service import (
    DEFAULT_ROUTING_PREFERENCE,
    ROUTING_PREFERENCE_KEY,
    get_routing_preference,
    set_routing_preference,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_get_routing_preference_default_when_unset(db_session):
    assert await get_routing_preference(db_session) == DEFAULT_ROUTING_PREFERENCE


@pytest.mark.asyncio
async def test_get_routing_preference_returns_stored_value(db_session):
    db_session.add(AppSetting(key=ROUTING_PREFERENCE_KEY, value="quality"))
    await db_session.commit()
    assert await get_routing_preference(db_session) == "quality"


@pytest.mark.asyncio
async def test_get_routing_preference_rejects_invalid_stored_value(db_session):
    db_session.add(AppSetting(key=ROUTING_PREFERENCE_KEY, value="garbage"))
    await db_session.commit()
    assert await get_routing_preference(db_session) == DEFAULT_ROUTING_PREFERENCE


@pytest.mark.asyncio
async def test_set_routing_preference_inserts_new_row(db_session):
    result = await set_routing_preference(db_session, "budget")
    assert result == "budget"
    row = (await db_session.scalars(select(AppSetting))).one()
    assert row.key == ROUTING_PREFERENCE_KEY
    assert row.value == "budget"


@pytest.mark.asyncio
async def test_set_routing_preference_updates_existing_row(db_session):
    db_session.add(AppSetting(key=ROUTING_PREFERENCE_KEY, value="quality"))
    await db_session.commit()
    result = await set_routing_preference(db_session, "balanced")
    assert result == "balanced"
    rows = list((await db_session.scalars(select(AppSetting))).all())
    assert len(rows) == 1
    assert rows[0].value == "balanced"


@pytest.mark.asyncio
async def test_set_routing_preference_rejects_invalid_value(db_session):
    with pytest.raises(ValueError, match="无效的偏好值"):
        await set_routing_preference(db_session, "fastest")
    # nothing persisted
    rows = list((await db_session.scalars(select(AppSetting))).all())
    assert rows == []
