"""Public resolver contract for owner-scoped task model bindings."""

import pytest
from app.core.security import hash_password
from app.models import AIModelConfig, AgentTaskModelBinding, User
from app.services.agent_settings_service import resolve_task_model

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_resolve_task_model_is_active_and_owner_scoped(db_session):
    owner = User(
        username="resolver-owner",
        email="resolver-owner@example.com",
        hashed_password=hash_password("testpass123"),
    )
    db_session.add(owner)
    await db_session.flush()
    foreign = User(
        username="resolver-foreign-owner",
        email="resolver-foreign-owner@example.com",
        hashed_password=hash_password("testpass123"),
    )
    db_session.add(foreign)
    await db_session.flush()

    active = AIModelConfig(
        owner_id=owner.id,
        name="resolver-active",
        provider="openai",
        model_id="resolver-active-model",
        is_active=True,
    )
    inactive = AIModelConfig(
        owner_id=owner.id,
        name="resolver-inactive",
        provider="openai",
        model_id="resolver-inactive-model",
        is_active=False,
    )
    foreign_model = AIModelConfig(
        owner_id=foreign.id,
        name="resolver-foreign",
        provider="openai",
        model_id="resolver-foreign-model",
        is_active=True,
    )
    db_session.add_all([active, inactive, foreign_model])
    await db_session.flush()
    db_session.add_all(
        [
            AgentTaskModelBinding(owner_id=owner.id, task="qa", model_id=active.id),
            AgentTaskModelBinding(
                owner_id=owner.id, task="deep_analysis", model_id=inactive.id
            ),
            AgentTaskModelBinding(
                owner_id=foreign.id, task="qa", model_id=foreign_model.id
            ),
        ]
    )
    await db_session.flush()

    assert (
        await resolve_task_model(db_session, owner_id=owner.id, task="qa")
    ).id == active.id
    assert (
        await resolve_task_model(db_session, owner_id=owner.id, task="deep_analysis")
    ) is None
    assert (
        await resolve_task_model(db_session, owner_id=owner.id, task="embedding")
    ) is None
