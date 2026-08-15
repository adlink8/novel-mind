"""Rule-based admission and owner-scoped retrieval of preference memories."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_settings import AgentSettings
from app.models.reader_chat import ReaderMessage
from app.models.user_preference_memory import UserPreferenceMemory


_CONCISE_RE = re.compile(
    r"(?:以后|今后|从现在起|接下来|默认|我偏好|我喜欢|我习惯).{0,20}"
    r"(?:简洁|精简|简短|短一点).{0,12}(?:回答|回复|说明|解释)?",
    re.IGNORECASE,
)
_CHINESE_RE = re.compile(
    r"(?:以后|今后|从现在起|接下来|默认|我偏好|我喜欢|我习惯).{0,20}"
    r"(?:用中文|中文回答|中文回复)",
    re.IGNORECASE,
)
_PREFERENCE_CONTEXT_MAX_ITEMS = 8
_ALLOWED_CONTEXT_VALUES = {
    "response_style": frozenset({"concise"}),
    "language": frozenset({"zh-CN"}),
}


def _candidates(body: str) -> list[tuple[str, str, float, bool]]:
    """Admit only explicit, stable preference language; never infer personality."""

    candidates: list[tuple[str, str, float, bool]] = []
    if _CONCISE_RE.search(body):
        candidates.append(("response_style", "concise", 1.0, True))
    if _CHINESE_RE.search(body):
        candidates.append(("language", "zh-CN", 1.0, True))
    return candidates


async def _memory_config(db: AsyncSession, *, owner_id: int) -> tuple[bool, int | None]:
    result = await db.execute(
        select(AgentSettings.memory_enabled, AgentSettings.memory_retention_days).where(
            AgentSettings.owner_id == owner_id
        )
    )
    row = result.one_or_none()
    if row is None:
        return False, None
    return bool(row[0]), row[1]


async def build_preference_context(
    db: AsyncSession, *, owner_id: int
) -> dict[str, list[dict[str, Any]] | list[int]]:
    """Return the bounded, auditable preference projection for one owner.

    The projection is intentionally not a memory dump: source messages are never
    selected, only explicit and unexpired rows with a fixed kind/value pair are
    converted into a short context item.
    """

    enabled, _retention_days = await _memory_config(db, owner_id=owner_id)
    if not enabled:
        return {"items": [], "memory_ids": []}

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(UserPreferenceMemory)
        .where(
            UserPreferenceMemory.owner_id == owner_id,
            UserPreferenceMemory.explicit.is_(True),
            or_(
                UserPreferenceMemory.expires_at.is_(None),
                UserPreferenceMemory.expires_at > now,
            ),
        )
        .order_by(UserPreferenceMemory.created_at.asc(), UserPreferenceMemory.id.asc())
        .limit(_PREFERENCE_CONTEXT_MAX_ITEMS * 2)
    )

    items: list[dict[str, Any]] = []
    for memory in result.scalars():
        allowed_values = _ALLOWED_CONTEXT_VALUES.get(memory.kind)
        if allowed_values is None or memory.value not in allowed_values:
            continue
        items.append(
            {
                "memory_id": memory.id,
                "kind": memory.kind,
                "value": memory.value,
            }
        )
        if len(items) >= _PREFERENCE_CONTEXT_MAX_ITEMS:
            break

    return {"items": items, "memory_ids": [item["memory_id"] for item in items]}


async def extract_from_persisted_message(
    db: AsyncSession, message_id: int
) -> list[UserPreferenceMemory]:
    """Extract memories from a message that is already present in the database."""

    message = await db.get(ReaderMessage, message_id)
    if message is None or message.role != "user":
        return []
    enabled, retention_days = await _memory_config(db, owner_id=message.owner_id)
    if not enabled:
        return []

    expires_at = None
    if retention_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=retention_days)

    created: list[UserPreferenceMemory] = []
    for kind, value, confidence, explicit in _candidates(message.body):
        existing = await db.scalar(
            select(UserPreferenceMemory).where(
                UserPreferenceMemory.source_message_id == message.id,
                UserPreferenceMemory.kind == kind,
                UserPreferenceMemory.value == value,
            )
        )
        if existing is not None:
            created.append(existing)
            continue
        memory = UserPreferenceMemory(
            owner_id=message.owner_id,
            source_message_id=message.id,
            kind=kind,
            value=value,
            confidence=confidence,
            explicit=explicit,
            expires_at=expires_at,
        )
        db.add(memory)
        created.append(memory)
    if created:
        await db.flush()
    return created


async def list_preference_memories(
    db: AsyncSession, *, owner_id: int
) -> list[UserPreferenceMemory]:
    enabled, _retention_days = await _memory_config(db, owner_id=owner_id)
    if not enabled:
        return []
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(UserPreferenceMemory)
        .where(
            UserPreferenceMemory.owner_id == owner_id,
            or_(
                UserPreferenceMemory.expires_at.is_(None),
                UserPreferenceMemory.expires_at > now,
            ),
        )
        .order_by(UserPreferenceMemory.created_at.asc(), UserPreferenceMemory.id.asc())
    )
    return list(result.scalars().all())


async def delete_preference_memory(
    db: AsyncSession, *, owner_id: int, memory_id: int
) -> bool:
    memory = await db.scalar(
        select(UserPreferenceMemory).where(
            UserPreferenceMemory.id == memory_id,
            UserPreferenceMemory.owner_id == owner_id,
        )
    )
    if memory is None:
        return False
    await db.delete(memory)
    await db.flush()
    return True


async def delete_all_preference_memories(db: AsyncSession, *, owner_id: int) -> int:
    result = await db.execute(
        delete(UserPreferenceMemory).where(UserPreferenceMemory.owner_id == owner_id)
    )
    await db.flush()
    return int(result.rowcount or 0)
