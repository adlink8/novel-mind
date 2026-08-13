"""Persistence service for owner-scoped Agent settings."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_settings import AgentSettings, AgentTaskModelBinding
from app.models.ai_model import AIModelConfig
from app.schemas.agent_settings import (
    AgentSettingsResponse,
    AgentSettingsUpdate,
    AgentTaskModelBindings,
)

DEFAULT_AGENT_SETTINGS = {
    "auto_deep_analysis": False,
    "memory_enabled": False,
    "memory_retention_days": None,
    "show_analysis_progress": True,
    "notify_analysis_complete": True,
    "auto_create_candidate_artifacts": False,
}
TASK_NAMES = ("qa", "deep_analysis", "continuation", "illustration", "rag_eval", "embedding")


async def resolve_task_model(
    db: AsyncSession, *, owner_id: int, task: str
) -> AIModelConfig | None:
    """Return the active model bound to ``task`` for exactly ``owner_id``.

    The active check is deliberately applied to the model row, so a stale
    binding never authorizes an inactive deployment or a model owned by anyone
    else.  Callers decide whether an unbound task falls back to the owner's
    default model.
    """
    if task not in TASK_NAMES:
        return None
    return await db.scalar(
        select(AIModelConfig)
        .join(
            AgentTaskModelBinding,
            AgentTaskModelBinding.model_id == AIModelConfig.id,
        )
        .where(
            AgentTaskModelBinding.owner_id == owner_id,
            AgentTaskModelBinding.task == task,
            AIModelConfig.owner_id == owner_id,
            AIModelConfig.is_active.is_(True),
        )
    )


async def _binding_values(db: AsyncSession, *, owner_id: int) -> AgentTaskModelBindings:
    result = await db.execute(
        select(AgentTaskModelBinding).where(
            AgentTaskModelBinding.owner_id == owner_id
        )
    )
    values = {task: None for task in TASK_NAMES}
    for binding in result.scalars():
        if binding.task in values:
            values[binding.task] = binding.model_id
    return AgentTaskModelBindings(**values)


async def get_agent_settings(
    db: AsyncSession, *, owner_id: int
) -> AgentSettingsResponse:
    result = await db.execute(
        select(AgentSettings).where(AgentSettings.owner_id == owner_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = AgentSettings(owner_id=owner_id, **DEFAULT_AGENT_SETTINGS)
        db.add(settings)
        await db.flush()

    return AgentSettingsResponse(
        auto_deep_analysis=settings.auto_deep_analysis,
        memory_enabled=settings.memory_enabled,
        memory_retention_days=settings.memory_retention_days,
        show_analysis_progress=settings.show_analysis_progress,
        notify_analysis_complete=settings.notify_analysis_complete,
        auto_create_candidate_artifacts=settings.auto_create_candidate_artifacts,
        task_model_bindings=await _binding_values(db, owner_id=owner_id),
    )


async def set_agent_settings(
    db: AsyncSession, *, owner_id: int, data: AgentSettingsUpdate
) -> AgentSettingsResponse:
    result = await db.execute(
        select(AgentSettings).where(AgentSettings.owner_id == owner_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = AgentSettings(owner_id=owner_id)
        db.add(settings)

    settings.auto_deep_analysis = data.auto_deep_analysis
    settings.memory_enabled = data.memory_enabled
    settings.memory_retention_days = data.memory_retention_days
    settings.show_analysis_progress = data.show_analysis_progress
    settings.notify_analysis_complete = data.notify_analysis_complete
    settings.auto_create_candidate_artifacts = data.auto_create_candidate_artifacts

    requested = data.task_model_bindings.model_dump()
    model_ids = {model_id for model_id in requested.values() if model_id is not None}
    if model_ids:
        result = await db.execute(
            select(AIModelConfig.id).where(
                AIModelConfig.owner_id == owner_id,
                AIModelConfig.id.in_(model_ids),
                AIModelConfig.is_active.is_(True),
            )
        )
        owned_ids = set(result.scalars().all())
        if owned_ids != model_ids:
            raise ValueError("任务绑定的模型不存在或不属于当前用户")

    await db.execute(
        delete(AgentTaskModelBinding).where(
            AgentTaskModelBinding.owner_id == owner_id
        )
    )
    for task, model_id in requested.items():
        if model_id is not None:
            db.add(
                AgentTaskModelBinding(
                    owner_id=owner_id, task=task, model_id=model_id
                )
            )
    await db.flush()
    return AgentSettingsResponse(
        **data.model_dump(exclude={"task_model_bindings"}),
        task_model_bindings=await _binding_values(db, owner_id=owner_id),
    )
