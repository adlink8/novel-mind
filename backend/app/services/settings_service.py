"""应用设置服务 - app_settings 键值表读写。"""

import json
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

ROUTING_PREFERENCE_KEY = "routing_preference"
DEFAULT_ROUTING_PREFERENCE = "balanced"
VALID_ROUTING_PREFERENCES = ("quality", "balanced", "budget")

READER_BUDGET_DEFAULTS_KEY_PREFIX = "reader_budget_defaults:"
NARRATIVE_MEMORY_ARC_WINDOW_KEY_PREFIX = "narrative_memory_arc_window_size:"
DEFAULT_ARC_WINDOW_SIZE = 3

DEFAULT_READER_BUDGETS = {
    "conversation": {
        "max_calls": 40,
        "max_input_tokens": 400_000,
        "max_output_tokens": 80_000,
        "max_cost_usd": Decimal("5.00"),
    },
    "novel": {
        "max_calls": 400,
        "max_input_tokens": 4_000_000,
        "max_output_tokens": 800_000,
        "max_cost_usd": Decimal("50.00"),
    },
}


def _budget_key(owner_id: int) -> str:
    return f"{READER_BUDGET_DEFAULTS_KEY_PREFIX}{owner_id}"


def _arc_window_key(owner_id: int, novel_id: int | None = None) -> str:
    suffix = str(novel_id) if novel_id is not None else "default"
    return f"{NARRATIVE_MEMORY_ARC_WINDOW_KEY_PREFIX}{owner_id}:{suffix}"


def _copy_default_budgets() -> dict[str, dict[str, int | Decimal]]:
    return {
        scope: dict(values) for scope, values in DEFAULT_READER_BUDGETS.items()
    }


def _normalise_budget_defaults(raw: object) -> dict[str, dict[str, int | Decimal]]:
    result = _copy_default_budgets()
    if not isinstance(raw, dict):
        return result
    for scope in ("conversation", "novel"):
        candidate = raw.get(scope)
        if not isinstance(candidate, dict):
            continue
        target = result[scope]
        for field in ("max_calls", "max_input_tokens", "max_output_tokens"):
            value = candidate.get(field)
            if isinstance(value, int) and value > 0:
                target[field] = value
        value = candidate.get("max_cost_usd")
        try:
            cost = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            cost = Decimal(0)
        if cost > 0:
            target["max_cost_usd"] = cost
    return result


def budget_policy_payload(policy: object) -> dict[str, int | str]:
    """将 BudgetPolicy 或 ORM ledger 转成 API/设置存储使用的稳定形状。"""
    if isinstance(policy, Mapping):
        return {
            "max_calls": int(policy["max_calls"]),
            "max_input_tokens": int(policy["max_input_tokens"]),
            "max_output_tokens": int(policy["max_output_tokens"]),
            "max_cost_usd": str(Decimal(str(policy["max_cost_usd"]))),
        }
    return {
        "max_calls": int(policy.max_calls),
        "max_input_tokens": int(policy.max_input_tokens),
        "max_output_tokens": int(policy.max_output_tokens),
        "max_cost_usd": str(Decimal(policy.max_cost_usd)),
    }


async def _get_setting_value(db: AsyncSession, key: str) -> str | None:
    return await db.scalar(select(AppSetting.value).where(AppSetting.key == key))


async def _upsert_setting(db: AsyncSession, key: str, value: str) -> None:
    setting = await db.scalar(select(AppSetting).where(AppSetting.key == key))
    if setting is None:
        db.add(AppSetting(key=key, value=value))
    else:
        setting.value = value


async def get_reader_budget_defaults(
    db: AsyncSession, owner_id: int
) -> dict[str, dict[str, int | Decimal]]:
    raw_value = await _get_setting_value(db, _budget_key(owner_id))
    if not raw_value:
        return _copy_default_budgets()
    try:
        raw = json.loads(raw_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _copy_default_budgets()
    return _normalise_budget_defaults(raw)


async def set_reader_budget_defaults(
    db: AsyncSession,
    owner_id: int,
    budgets: dict[str, dict[str, int | Decimal]],
) -> dict[str, dict[str, int | Decimal]]:
    normalised = _normalise_budget_defaults(budgets)
    encoded = {
        scope: budget_policy_payload(values)
        for scope, values in normalised.items()
    }
    await _upsert_setting(
        db,
        _budget_key(owner_id),
        json.dumps(encoded, ensure_ascii=False, separators=(",", ":")),
    )
    return normalised


async def get_arc_window_size(
    db: AsyncSession, owner_id: int, novel_id: int | None = None
) -> int:
    """读取小说级窗口设置；缺失时返回兼容旧行为的 3。"""
    keys = []
    if novel_id is not None:
        keys.append(_arc_window_key(owner_id, novel_id))
    keys.append(_arc_window_key(owner_id))
    for key in keys:
        raw = await _get_setting_value(db, key)
        try:
            value = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            value = 0
        if 1 <= value <= 5:
            return value
    return DEFAULT_ARC_WINDOW_SIZE


async def set_arc_window_size(
    db: AsyncSession,
    owner_id: int,
    value: int,
    novel_id: int | None = None,
) -> int:
    if not 1 <= value <= 5:
        raise ValueError("arc_window_size must be between 1 and 5")
    await _upsert_setting(db, _arc_window_key(owner_id, novel_id), str(value))
    return value


async def get_routing_preference(db: AsyncSession) -> str:
    """读取路由偏好；未设置时返回默认值 "balanced"。"""
    result = await db.execute(
        select(AppSetting.value).where(AppSetting.key == ROUTING_PREFERENCE_KEY)
    )
    value = result.scalar_one_or_none()
    if value in VALID_ROUTING_PREFERENCES:
        return value
    return DEFAULT_ROUTING_PREFERENCE


async def set_routing_preference(db: AsyncSession, preference: str) -> str:
    """
    写入路由偏好（upsert）。

    Args:
        db: 数据库会话
        preference: 偏好值，必须是 quality / balanced / budget 之一

    Returns:
        已写入的偏好值

    Raises:
        ValueError: 非法偏好值
    """
    if preference not in VALID_ROUTING_PREFERENCES:
        raise ValueError(
            f"无效的偏好值: {preference}，可选: quality / balanced / budget"
        )
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == ROUTING_PREFERENCE_KEY)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        db.add(AppSetting(key=ROUTING_PREFERENCE_KEY, value=preference))
    else:
        setting.value = preference
    await db.flush()
    return preference
