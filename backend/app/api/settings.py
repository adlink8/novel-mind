"""
设置中心 API 路由

端点列表:
  GET /api/settings/routing - 获取 AI 路由全局偏好
  PUT /api/settings/routing - 更新 AI 路由全局偏好（同步更新内存中的 ai_router）

说明:
  - 偏好持久化在 app_settings 键值表（key = routing_preference）
  - 应用启动时从库中读取并恢复（见 app/main.py lifespan）
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.models.reader_chat import ReaderBudgetLedger, ReaderConversation
from app.schemas.settings import (
    AIBudgetLimits,
    AIBudgetResponse,
    AIBudgetUpdate,
    RoutingPreferenceResponse,
    RoutingPreferenceUpdate,
)
from app.services.ai_router import ai_router
from app.services.settings_service import (
    budget_policy_payload,
    get_arc_window_size,
    get_reader_budget_defaults,
    get_routing_preference,
    set_arc_window_size,
    set_reader_budget_defaults,
    set_routing_preference,
)

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/routing", response_model=RoutingPreferenceResponse)
async def get_routing(db: AsyncSession = Depends(get_db)):
    """获取当前 AI 路由全局偏好（未设置时返回默认 "balanced"）"""
    preference = await get_routing_preference(db)
    return RoutingPreferenceResponse(preference=preference)


@router.put("/routing", response_model=RoutingPreferenceResponse)
async def update_routing(
    data: RoutingPreferenceUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新 AI 路由全局偏好（落库 + 同步内存中的路由器单例）"""
    preference = await set_routing_preference(db, data.preference)
    ai_router.update_preference(preference)
    return RoutingPreferenceResponse(preference=preference)


async def _owned_novel(
    db: AsyncSession, novel_id: int, current_user: User
) -> Novel:
    novel = await db.scalar(select(Novel).where(Novel.id == novel_id))
    if novel is None or (not current_user.is_superuser and novel.owner_id != current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="小说不存在")
    return novel


def _limits(value: object) -> AIBudgetLimits:
    return AIBudgetLimits.model_validate(budget_policy_payload(value))


def _ledger_limits(ledger: ReaderBudgetLedger) -> AIBudgetLimits:
    return AIBudgetLimits.model_validate(
        {
            "max_calls": ledger.max_calls,
            "max_input_tokens": ledger.max_input_tokens,
            "max_output_tokens": ledger.max_output_tokens,
            "max_cost_usd": ledger.max_cost_usd,
        }
    )


@router.get("/ai-budget", response_model=AIBudgetResponse)
async def get_ai_budget(
    novel_id: int | None = Query(default=None, gt=0),
    conversation_id: int | None = Query(default=None, gt=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> AIBudgetResponse:
    if novel_id is not None and conversation_id is not None:
        raise HTTPException(status_code=422, detail="novel_id 和 conversation_id 不能同时设置")

    defaults = await get_reader_budget_defaults(db, current_user.id)
    selected_novel_id = novel_id
    conversation_limits = _limits(defaults["conversation"])
    novel_limits = _limits(defaults["novel"])
    scope = "defaults"

    if conversation_id is not None:
        conversation = await db.scalar(
            select(ReaderConversation).where(ReaderConversation.id == conversation_id)
        )
        if conversation is None or (
            not current_user.is_superuser and conversation.owner_id != current_user.id
        ):
            raise HTTPException(status_code=404, detail="会话不存在")
        selected_novel_id = conversation.novel_id
        scope = "conversation"
        conversation_ledger = await db.scalar(
            select(ReaderBudgetLedger).where(
                ReaderBudgetLedger.scope_type == "conversation",
                ReaderBudgetLedger.conversation_id == conversation.id,
                ReaderBudgetLedger.owner_id == current_user.id,
            )
        )
        if conversation_ledger is not None:
            conversation_limits = _ledger_limits(conversation_ledger)

    if selected_novel_id is not None:
        novel = await _owned_novel(db, selected_novel_id, current_user)
        scope = "novel" if conversation_id is None else scope
        novel_ledger = await db.scalar(
            select(ReaderBudgetLedger).where(
                ReaderBudgetLedger.scope_type == "novel",
                ReaderBudgetLedger.owner_id == current_user.id,
                ReaderBudgetLedger.novel_id == novel.id,
            )
        )
        if novel_ledger is not None:
            novel_limits = _ledger_limits(novel_ledger)

    return AIBudgetResponse(
        conversation=conversation_limits,
        novel=novel_limits,
        arc_window_size=await get_arc_window_size(
            db, current_user.id, selected_novel_id
        ),
        scope=scope,
        novel_id=selected_novel_id,
        conversation_id=conversation_id,
    )


@router.put("/ai-budget", response_model=AIBudgetResponse)
async def update_ai_budget(
    data: AIBudgetUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> AIBudgetResponse:
    defaults = await get_reader_budget_defaults(db, current_user.id)

    if data.conversation_id is None and data.novel_id is None:
        previous_defaults = {
            scope: dict(values) for scope, values in defaults.items()
        }
        if data.conversation is not None:
            defaults["conversation"] = data.conversation.model_dump()
        if data.novel is not None:
            defaults["novel"] = data.novel.model_dump()
        await set_reader_budget_defaults(db, current_user.id, defaults)
        # 默认值变更立即作用于已有 ledger；之后仍可通过指定小说/会话单独覆盖。
        ledgers = (
            await db.scalars(
                select(ReaderBudgetLedger).where(
                    ReaderBudgetLedger.owner_id == current_user.id
                )
            )
        ).all()
        for ledger in ledgers:
            previous_policy = previous_defaults[ledger.scope_type]
            current_policy = {
                "max_calls": ledger.max_calls,
                "max_input_tokens": ledger.max_input_tokens,
                "max_output_tokens": ledger.max_output_tokens,
                "max_cost_usd": ledger.max_cost_usd,
            }
            # 仅刷新仍沿用旧默认值的 ledger；明确配置过的小说/会话保持独立上限。
            if not (
                int(current_policy["max_calls"])
                == int(previous_policy["max_calls"])
                and int(current_policy["max_input_tokens"])
                == int(previous_policy["max_input_tokens"])
                and int(current_policy["max_output_tokens"])
                == int(previous_policy["max_output_tokens"])
                and Decimal(str(current_policy["max_cost_usd"]))
                == Decimal(str(previous_policy["max_cost_usd"]))
            ):
                continue
            policy = defaults[ledger.scope_type]
            ledger.max_calls = int(policy["max_calls"])
            ledger.max_input_tokens = int(policy["max_input_tokens"])
            ledger.max_output_tokens = int(policy["max_output_tokens"])
            ledger.max_cost_usd = policy["max_cost_usd"]
        if data.arc_window_size is not None:
            await set_arc_window_size(db, current_user.id, data.arc_window_size)
    elif data.conversation_id is not None:
        conversation = await db.scalar(
            select(ReaderConversation).where(ReaderConversation.id == data.conversation_id)
        )
        if conversation is None or (
            not current_user.is_superuser and conversation.owner_id != current_user.id
        ):
            raise HTTPException(status_code=404, detail="会话不存在")
        policy = data.conversation
        assert policy is not None
        ledger = await db.scalar(
            select(ReaderBudgetLedger).where(
                ReaderBudgetLedger.scope_type == "conversation",
                ReaderBudgetLedger.conversation_id == conversation.id,
                ReaderBudgetLedger.owner_id == current_user.id,
            )
        )
        if ledger is None:
            ledger = ReaderBudgetLedger(
                scope_type="conversation",
                owner_id=current_user.id,
                novel_id=conversation.novel_id,
                conversation_id=conversation.id,
                **{
                    key: int(value) if key != "max_cost_usd" else value
                    for key, value in policy.model_dump().items()
                },
            )
            db.add(ledger)
        else:
            ledger.max_calls = policy.max_calls
            ledger.max_input_tokens = policy.max_input_tokens
            ledger.max_output_tokens = policy.max_output_tokens
            ledger.max_cost_usd = policy.max_cost_usd
    else:
        novel = await _owned_novel(db, data.novel_id, current_user)
        if data.novel is not None:
            policy = data.novel
            ledger = await db.scalar(
                select(ReaderBudgetLedger).where(
                    ReaderBudgetLedger.scope_type == "novel",
                    ReaderBudgetLedger.owner_id == current_user.id,
                    ReaderBudgetLedger.novel_id == novel.id,
                )
            )
            if ledger is None:
                ledger = ReaderBudgetLedger(
                    scope_type="novel",
                    owner_id=current_user.id,
                    novel_id=novel.id,
                    conversation_id=None,
                    **{
                        key: int(value) if key != "max_cost_usd" else value
                        for key, value in policy.model_dump().items()
                    },
                )
                db.add(ledger)
            else:
                ledger.max_calls = policy.max_calls
                ledger.max_input_tokens = policy.max_input_tokens
                ledger.max_output_tokens = policy.max_output_tokens
                ledger.max_cost_usd = policy.max_cost_usd
        if data.arc_window_size is not None:
            await set_arc_window_size(
                db, current_user.id, data.arc_window_size, novel_id=novel.id
            )

    await db.flush()
    return await get_ai_budget(
        novel_id=(data.novel_id if data.conversation_id is None else None),
        conversation_id=data.conversation_id,
        db=db,
        current_user=current_user,
    )
