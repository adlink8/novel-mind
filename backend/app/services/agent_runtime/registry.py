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
from app.schemas.agent_runtime import SkillRuntimeManifest, SkillVersionRegister
from app.services.agent_tools.catalog import TOOL_CAPABILITY_NAMES
from app.services.agent_runtime.builtin_manifests import builtin_skill_manifests
from app.services.tool_connectors.service import CONNECTOR_TOOL_PREFIX, connector_slug

# 25.2-02 冻结的 7 个域工具集（唯一事实源在 facade.TOOL_NAMES，这里镜像成集合）。
REGISTERED_DOMAIN_TOOLS: frozenset[str] = TOOL_CAPABILITY_NAMES

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


def runtime_manifest_payload(
    *,
    name: str,
    version: str,
    description: str | None,
    prompt: str,
    allowed_tools: list[str],
    read_permissions: list[str],
    write_permissions: list[str],
    forbidden_spaces: list[str],
    budget: dict[str, Any],
    approval_required_for: list[str],
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    execution_mode: str = "declarative_only",
) -> dict[str, Any]:
    """构造跨 Python/TypeScript 共用的 DB declarative runtime payload。"""
    return {
        "name": name,
        "version": version,
        "description": description or "",
        "prompt": prompt,
        "execution_mode": execution_mode,
        "allowed_tools": list(allowed_tools),
        "read_permissions": list(read_permissions),
        "write_permissions": list(write_permissions),
        "forbidden_spaces": list(forbidden_spaces),
        "budget": dict(budget),
        "approval_required_for": list(approval_required_for),
        "input_schema": dict(input_schema),
        "output_schema": dict(output_schema),
    }


def runtime_manifest_checksum(payload: dict[str, Any]) -> str:
    return contract_yaml_checksum(payload)


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
    unknown: list[Any] = []
    for tool in allowed_tools:
        if not isinstance(tool, str):
            unknown.append(tool)
        elif tool in REGISTERED_DOMAIN_TOOLS:
            continue
        elif tool.startswith(CONNECTOR_TOOL_PREFIX):
            try:
                connector_slug(tool)
            except Exception:
                unknown.append(tool)
        else:
            unknown.append(tool)
    if unknown:
        raise SkillContractError(
            f"allowed_tools contains tools outside the Tool Capability Catalog: "
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
    checksum_payload = runtime_manifest_payload(
        name=contract.name,
        version=contract.version,
        description=contract.description,
        prompt=contract.prompt,
        allowed_tools=contract.allowed_tools,
        read_permissions=contract.read_permissions,
        write_permissions=contract.write_permissions,
        forbidden_spaces=contract.forbidden_spaces,
        budget=contract.budget,
        approval_required_for=contract.approval_required_for,
        input_schema=contract.input_schema,
        output_schema=contract.output_schema,
        execution_mode="declarative_only",
    )
    checksum = runtime_manifest_checksum(checksum_payload)

    # The current table predates a prompt column. Keep the prompt inside the
    # existing JSON schema envelope; the API unwraps this reserved key again.
    stored_input_schema = dict(contract.input_schema)
    stored_input_schema["x-novelmind-declarative-prompt"] = contract.prompt

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
        input_schema=stored_input_schema,
        output_schema=dict(contract.output_schema),
        execution_mode="declarative_only",
        status="active",
    )
    db.add(version)
    await db.flush()
    return registry, version


async def ensure_builtin_skills(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
) -> list[SkillVersion]:
    """Create allowlisted builtin Skill rows for one novel, without reopening disables."""
    rows: list[SkillVersion] = []
    for manifest in builtin_skill_manifests():
        name = str(manifest["name"])
        version_name = str(manifest["version"])
        registry = await db.scalar(
            select(SkillRegistry).where(
                SkillRegistry.owner_id == owner_id,
                SkillRegistry.novel_id == novel_id,
                SkillRegistry.name == name,
            )
        )
        if registry is None:
            registry = SkillRegistry(
                owner_id=owner_id,
                novel_id=novel_id,
                name=name,
                description=str(manifest.get("description") or ""),
                status="active",
            )
            db.add(registry)
            await db.flush()

        row = await db.scalar(
            select(SkillVersion).where(
                SkillVersion.registry_id == registry.id,
                SkillVersion.version == version_name,
            )
        )
        if row is None:
            # A pre-existing non-active registry without a version represents an
            # explicit disabled state; a read/backfill must not reopen it.
            if registry.status != "active":
                continue
            stored_input_schema = dict(manifest["input_schema"])
            stored_input_schema["x-novelmind-declarative-prompt"] = manifest["prompt"]
            row = SkillVersion(
                registry_id=registry.id,
                owner_id=owner_id,
                novel_id=novel_id,
                name=name,
                version=version_name,
                description=str(manifest.get("description") or ""),
                yaml_checksum=str(manifest["checksum"]),
                allowed_tools=list(manifest["allowed_tools"]),
                read_permissions=list(manifest.get("read_permissions") or []),
                write_permissions=list(manifest.get("write_permissions") or []),
                forbidden_spaces=list(manifest.get("forbidden_spaces") or []),
                budget=dict(manifest.get("budget") or {}),
                approval_required_for=list(manifest.get("approval_required_for") or []),
                input_schema=stored_input_schema,
                output_schema=dict(manifest["output_schema"]),
                execution_mode="builtin",
                status="active",
            )
            db.add(row)
            await db.flush()
        elif row.yaml_checksum != manifest["checksum"]:
            raise SkillContractError(
                f"builtin skill contract drift for {name}@{version_name}; "
                "run sync_builtin_skill_manifests"
            )
        rows.append(row)
    return rows


def skill_version_view_payload(row: SkillVersion) -> dict[str, Any]:
    """Project the legacy JSON storage back to the public declarative contract."""
    is_builtin = row.execution_mode == "builtin"
    payload = {
        "id": row.id,
        "registry_id": row.registry_id,
        "owner_id": row.owner_id,
        "novel_id": row.novel_id,
        "name": row.name,
        "version": row.version,
        "description": row.description,
        "yaml_checksum": row.yaml_checksum,
        "allowed_tools": list(row.allowed_tools or []),
        "read_permissions": list(row.read_permissions or []),
        "write_permissions": list(row.write_permissions or []),
        "forbidden_spaces": list(row.forbidden_spaces or []),
        "budget": dict(row.budget or {}),
        "approval_required_for": list(row.approval_required_for or []),
        "input_schema": dict(row.input_schema or {}),
        "output_schema": dict(row.output_schema or {}),
        "execution_mode": row.execution_mode,
        "status": row.status,
        "created_at": row.created_at,
        "execution_status": "active_runtime" if is_builtin else "declarative_only",
        "runtime_note": (
            "内置 Skill 已激活，可由 Pi runtime 调用。"
            if is_builtin
            else "已注册声明式 Skill；当前运行时不会执行 prompt 正文。"
        ),
    }
    payload["prompt"] = payload["input_schema"].pop(
        "x-novelmind-declarative-prompt", ""
    )
    return payload


def skill_runtime_manifest(row: SkillVersion) -> SkillRuntimeManifest:
    """只从 DB 版本行投影 canonical runtime manifest，不读取客户端路径。"""
    stored_input_schema = dict(row.input_schema or {})
    prompt = stored_input_schema.pop("x-novelmind-declarative-prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        raise SkillContractError("skill runtime manifest prompt is empty (fail closed)")
    allowed_tools = list(row.allowed_tools or [])
    if not allowed_tools:
        raise SkillContractError(
            "skill runtime manifest allowed_tools is invalid (fail closed)"
        )
    try:
        validate_skill_contract(
            {
                "name": row.name,
                "version": row.version,
                "allowed_tools": allowed_tools,
                "read_permissions": list(row.read_permissions or []),
                "write_permissions": list(row.write_permissions or []),
                "forbidden_spaces": list(row.forbidden_spaces or []),
                "budget": dict(row.budget or {}),
                "approval_required_for": list(row.approval_required_for or []),
                "input_schema": stored_input_schema,
                "output_schema": dict(row.output_schema or {}),
            }
        )
    except SkillContractError:
        raise SkillContractError(
            "skill runtime manifest allowed_tools is invalid (fail closed)"
        )
    payload = runtime_manifest_payload(
        name=row.name,
        version=row.version,
        description=row.description,
        prompt=prompt,
        allowed_tools=allowed_tools,
        read_permissions=list(row.read_permissions or []),
        write_permissions=list(row.write_permissions or []),
        forbidden_spaces=list(row.forbidden_spaces or []),
        budget=dict(row.budget or {}),
        approval_required_for=list(row.approval_required_for or []),
        input_schema=stored_input_schema,
        output_schema=dict(row.output_schema or {}),
        execution_mode=row.execution_mode,
    )
    checksum = runtime_manifest_checksum(payload)
    if checksum != row.yaml_checksum:
        raise SkillContractError("skill version checksum mismatch (fail closed)")
    return SkillRuntimeManifest(
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        skill_version_id=row.id,
        **payload,
        checksum=checksum,
    )


async def list_skills(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[SkillRegistry], int]:
    """列出当前用户可用的技能目录（元数据行）。"""
    where = [SkillRegistry.owner_id == owner_id]
    if novel_id is not None:
        where.append(SkillRegistry.novel_id == novel_id)
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
    novel_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[SkillVersion], int]:
    """列出某技能名的全部版本（含契约全文）。"""
    where = [
        SkillRegistry.owner_id == owner_id,
        SkillRegistry.name == skill_name,
        SkillVersion.registry_id == SkillRegistry.id,
    ]
    if novel_id is not None:
        where.extend(
            [
                SkillVersion.novel_id == novel_id,
                SkillRegistry.novel_id == novel_id,
            ]
        )
    total = await db.scalar(
        select(func.count()).select_from(SkillVersion).where(*where)
    )
    rows = list(
        (
            await db.scalars(
                select(SkillVersion)
                .join(SkillRegistry, SkillVersion.registry_id == SkillRegistry.id)
                .where(*where)
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
    novel_id: int,
    skill_version_id: int,
) -> SkillVersion | None:
    """读取指定技能版本（owner 隔离，404-hide 由调用方处理）。"""
    return await db.scalar(
        select(SkillVersion)
        .join(SkillRegistry, SkillVersion.registry_id == SkillRegistry.id)
        .where(
            SkillVersion.id == skill_version_id,
            SkillVersion.novel_id == novel_id,
            SkillRegistry.owner_id == owner_id,
            SkillRegistry.novel_id == novel_id,
        )
    )


async def set_skill_version_status(
    db: AsyncSession,
    *,
    owner_id: int,
    skill_name: str,
    skill_version_id: int,
    status: str,
) -> SkillVersion | None:
    """Change one owner-scoped version state and keep registry status honest."""
    if status not in {"draft", "active", "deprecated"}:
        raise SkillContractError(f"unsupported skill version status: {status}")
    row = await db.scalar(
        select(SkillVersion)
        .join(SkillRegistry, SkillVersion.registry_id == SkillRegistry.id)
        .where(
            SkillVersion.id == skill_version_id,
            SkillVersion.novel_id == SkillRegistry.novel_id,
            SkillRegistry.owner_id == owner_id,
            SkillRegistry.name == skill_name,
        )
    )
    if row is None:
        return None
    row.status = status
    registry = await db.scalar(
        select(SkillRegistry).where(SkillRegistry.id == row.registry_id)
    )
    if registry is not None:
        active_count = await db.scalar(
            select(func.count())
            .select_from(SkillVersion)
            .where(
                SkillVersion.registry_id == row.registry_id,
                SkillVersion.status == "active",
            )
        )
        registry.status = "active" if active_count else status
    await db.flush()
    return row
