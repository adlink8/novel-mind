"""技能注册服务：D-09 契约校验 + fail-closed allowed_tools 白名单门禁。

安全边界（T-25.2-03-01）:
  - allowed_tools 中任何不在 25.2-02 注册的 7 个域工具集里的名字 →
    注册拒绝、不产生任何 active 行（fail closed）。
  - yaml_checksum 由契约 payload 的规范化序列化计算（String(64)），
    作为 skill.yaml 内容指纹，供重放追溯。
  - 25.2-05 loader 会在加载侧再加一道同样的 fail-closed 校验（纵深防御）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_runtime import SkillRegistry, SkillVersion
from app.schemas.agent_runtime import SkillVersionRegister
from app.services.agent_tools.facade import TOOL_NAMES

# 25.2-02 冻结的 7 个域工具集（唯一事实源在 facade.TOOL_NAMES，这里镜像成集合）。
REGISTERED_DOMAIN_TOOLS: frozenset[str] = frozenset(TOOL_NAMES)

# D-09 最小契约字段集：注册时全部必须存在。
SKILL_CONTRACT_REQUIRED_FIELDS: tuple[str, ...] = (
    "name",
    "version",
    "allowed_tools",
    "read_permissions",
    "write_permissions",
    "forbidden_spaces",
    "budget",
    "approval_required_for",
    "input_schema",
    "output_schema",
)


class SkillContractError(RuntimeError):
    """技能契约不合法 / 越权工具 → 注册被拒绝（fail closed）。"""


def contract_yaml_checksum(contract: dict[str, Any]) -> str:
    """对契约 payload 做规范化序列化并求 SHA-256（String(64)）。"""
    canonical = json.dumps(
        contract, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def canonical_input_hash(input_data: dict[str, Any]) -> str:
    """对运行输入做规范化序列化并求 SHA-256（重放追溯的 input_hash）。"""
    canonical = json.dumps(
        input_data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_skill_contract(contract: dict[str, Any]) -> None:
    """校验 D-09 最小字段集 + allowed_tools 白名单（fail closed）。"""
    missing = set(SKILL_CONTRACT_REQUIRED_FIELDS) - set(contract)
    if missing:
        raise SkillContractError(
            f"skill contract missing required fields: {sorted(missing)}"
        )
    allowed_tools = contract.get("allowed_tools")
    if not isinstance(allowed_tools, list) or not allowed_tools:
        raise SkillContractError("allowed_tools must be a non-empty list")
    unknown = [
        tool
        for tool in allowed_tools
        if not isinstance(tool, str) or tool not in REGISTERED_DOMAIN_TOOLS
    ]
    if unknown:
        raise SkillContractError(
            f"allowed_tools contains tools outside the registered 7-tool set: "
            f"{unknown} (fail closed, no row becomes active)"
        )


async def register_skill_version(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    contract: SkillVersionRegister,
) -> tuple[SkillRegistry, SkillVersion]:
    """注册技能版本：先校验契约，再 upsert registry + 追加不可变 version。

    任何校验失败都在落库前抛 SkillContractError——不会产生 active 行。
    """
    payload = contract.model_dump()
    validate_skill_contract(payload)
    checksum = contract_yaml_checksum(payload)

    # registry：owner+novel 范围内技能名唯一（存在则复用）。
    registry = await db.scalar(
        select(SkillRegistry).where(
            SkillRegistry.owner_id == owner_id,
            SkillRegistry.novel_id == novel_id,
            SkillRegistry.name == contract.name,
        )
    )
    if registry is None:
        registry = SkillRegistry(
            owner_id=owner_id,
            novel_id=novel_id,
            name=contract.name,
            description=contract.description,
            status="active",
        )
        db.add(registry)
        await db.flush()

    version = SkillVersion(
        registry_id=registry.id,
        owner_id=owner_id,
        novel_id=novel_id,
        name=contract.name,
        version=contract.version,
        description=contract.description,
        yaml_checksum=checksum,
        allowed_tools=list(contract.allowed_tools),
        read_permissions=list(contract.read_permissions),
        write_permissions=list(contract.write_permissions),
        forbidden_spaces=list(contract.forbidden_spaces),
        budget=dict(contract.budget),
        approval_required_for=list(contract.approval_required_for),
        input_schema=dict(contract.input_schema),
        output_schema=dict(contract.output_schema),
        status="active",
    )
    db.add(version)
    await db.flush()
    return registry, version


async def list_skills(
    db: AsyncSession,
    *,
    owner_id: int,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[SkillRegistry], int]:
    """列出当前用户可用的技能目录（元数据行）。"""
    where = (SkillRegistry.owner_id == owner_id,)
    total = await db.scalar(
        select(func.count()).select_from(SkillRegistry).where(*where)
    )
    rows = list(
        (
            await db.scalars(
                select(SkillRegistry)
                .where(*where)
                .order_by(SkillRegistry.id)
                .offset(skip)
                .limit(limit)
            )
        ).all()
    )
    return rows, int(total or 0)


async def list_skill_versions(
    db: AsyncSession,
    *,
    owner_id: int,
    skill_name: str,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[SkillVersion], int]:
    """列出某技能名的全部版本（含契约全文）。"""
    where = (
        SkillRegistry.owner_id == owner_id,
        SkillRegistry.name == skill_name,
        SkillVersion.registry_id == SkillRegistry.id,
    )
    total = await db.scalar(
        select(func.count()).select_from(SkillVersion).where(*where)
    )
    rows = list(
        (
            await db.scalars(
                select(SkillVersion)
                .join(SkillRegistry, SkillVersion.registry_id == SkillRegistry.id)
                .where(
                    SkillRegistry.owner_id == owner_id, SkillRegistry.name == skill_name
                )
                .order_by(SkillVersion.id)
                .offset(skip)
                .limit(limit)
            )
        ).all()
    )
    return rows, int(total or 0)


async def get_skill_version(
    db: AsyncSession,
    *,
    owner_id: int,
    skill_version_id: int,
) -> SkillVersion | None:
    """读取指定技能版本（owner 隔离，404-hide 由调用方处理）。"""
    return await db.scalar(
        select(SkillVersion)
        .join(SkillRegistry, SkillVersion.registry_id == SkillRegistry.id)
        .where(
            SkillVersion.id == skill_version_id,
            SkillRegistry.owner_id == owner_id,
        )
    )
