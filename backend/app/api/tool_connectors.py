"""Desktop CRUD, validation, and fake-adapter dry-run for restricted Tools."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models import SkillRun, ToolConnectorVersion, User
from app.schemas.tool_connectors import (
    ToolConnectorListResponse,
    ToolConnectorPayload,
    ToolConnectorStatusUpdate,
    ToolConnectorView,
    ToolDryRunRequest,
    ToolDryRunResponse,
)
from app.services.tool_connectors.http_adapter import (
    FakeHttpAdapter,
    HttpAdapterResponse,
)
from app.services.tool_connectors.policy import ConnectorPolicyError
from app.services.tool_connectors.service import (
    append_connector_version,
    create_connector,
    dry_run_connector,
    connector_checksum,
    connector_slug,
    connector_url,
    execute_frozen_connector,
    latest_version,
    set_connector_status,
    validate_connector,
)

router = APIRouter(dependencies=[Depends(require_user)])
run_router = APIRouter()
_run_bearer = HTTPBearer(auto_error=False)


async def _require_connector_run(
    novel_id: int,
    credentials: HTTPAuthorizationCredentials | None = Depends(_run_bearer),
    db: AsyncSession = Depends(get_db),
) -> SkillRun:
    """Authenticate only the active per-run token; browser JWTs do not qualify."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="需要运行令牌")
    token_hash = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    run = await db.scalar(
        select(SkillRun).where(
            SkillRun.internal_token_hash == token_hash,
            SkillRun.novel_id == novel_id,
            SkillRun.status.in_(("queued", "running")),
        )
    )
    if run is None:
        raise HTTPException(status_code=401, detail="无效的运行令牌")
    return run


def _view(version: ToolConnectorVersion) -> ToolConnectorView:
    return ToolConnectorView(
        id=version.connector_id,
        connector_id=version.connector_id,
        version_id=version.id,
        owner_id=version.owner_id,
        version=version.version,
        name=version.name,
        description=version.description,
        base_url=version.base_url,
        path=version.path,
        method=version.method,
        request_schema=version.request_schema,
        response_schema=version.response_schema,
        enabled=version.enabled,
        status=version.status,
        created_at=version.created_at,
    )


@router.post(
    "/tools", response_model=ToolConnectorView, status_code=status.HTTP_201_CREATED
)
async def create_tool_connector(
    payload: ToolConnectorPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    try:
        return _view(
            await create_connector(db, owner_id=current_user.id, payload=payload)
        )
    except ConnectorPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tools", response_model=ToolConnectorListResponse)
async def list_tool_connectors(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(require_user)
):
    rows = list(
        await db.scalars(
            select(ToolConnectorVersion)
            .where(ToolConnectorVersion.owner_id == current_user.id)
            .order_by(
                ToolConnectorVersion.connector_id, ToolConnectorVersion.version.desc()
            )
        )
    )
    latest_by_connector: dict[int, ToolConnectorVersion] = {}
    for row in rows:
        latest_by_connector.setdefault(row.connector_id, row)
    items = list(latest_by_connector.values())
    return {"items": [_view(row) for row in items], "total": len(items)}


async def _owned_latest(
    db: AsyncSession, owner_id: int, connector_id: int
) -> ToolConnectorVersion:
    row = await latest_version(db, owner_id=owner_id, connector_id=connector_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tool connector not found")
    return row


@router.get("/tools/{connector_id}", response_model=ToolConnectorView)
async def get_tool_connector(
    connector_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    return _view(await _owned_latest(db, current_user.id, connector_id))


@router.put("/tools/{connector_id}", response_model=ToolConnectorView)
async def update_tool_connector(
    connector_id: int,
    payload: ToolConnectorPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    try:
        row = await append_connector_version(
            db, owner_id=current_user.id, connector_id=connector_id, payload=payload
        )
    except ConnectorPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Tool connector not found")
    return _view(row)


@router.post("/tools/{connector_id}/validate", response_model=ToolConnectorView)
async def validate_tool_connector(
    connector_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    try:
        row = await validate_connector(
            db, owner_id=current_user.id, connector_id=connector_id
        )
    except ConnectorPolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Tool connector not found")
    return _view(row)


@router.patch("/tools/{connector_id}/status", response_model=ToolConnectorView)
async def update_tool_connector_status(
    connector_id: int,
    payload: ToolConnectorStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    try:
        row = await set_connector_status(
            db,
            owner_id=current_user.id,
            connector_id=connector_id,
            status=payload.status,
        )
    except ConnectorPolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Tool connector not found")
    return _view(row)


@router.post("/tools/{connector_id}/dry-run", response_model=ToolDryRunResponse)
async def dry_run_tool_connector(
    connector_id: int,
    payload: ToolDryRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    row = await _owned_latest(db, current_user.id, connector_id)
    adapter = FakeHttpAdapter(
        HttpAdapterResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b"{}",
            final_url=f"{row.base_url.rstrip('/')}{row.path}",
        )
    )
    try:
        return await dry_run_connector(row, request=payload.request, adapter=adapter)
    except ConnectorPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@run_router.post("/connectors/{connector_name}", response_model=ToolDryRunResponse)
async def run_frozen_tool_connector(
    connector_name: str,
    payload: ToolDryRunRequest,
    run: SkillRun = Depends(_require_connector_run),
    db: AsyncSession = Depends(get_db),
):
    """Run-token-only proxy; the request cannot supply a URL or connector version."""
    tool_name = f"connector:{connector_name}"
    try:
        connector_slug(tool_name)
    except ConnectorPolicyError as exc:
        raise HTTPException(status_code=404, detail="connector not found") from exc

    snapshots = (run.frozen_manifest or {}).get("connector_versions") or []
    snapshot = next(
        (item for item in snapshots if item.get("tool_name") == tool_name), None
    )
    if snapshot is None:
        raise HTTPException(
            status_code=404, detail="connector not enabled for this run"
        )

    row = await db.scalar(
        select(ToolConnectorVersion).where(
            ToolConnectorVersion.id == snapshot.get("version_id"),
            ToolConnectorVersion.connector_id == snapshot.get("connector_id"),
            ToolConnectorVersion.owner_id == run.owner_id,
            ToolConnectorVersion.name == connector_name,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="connector not found")
    if row.status != "active" or not row.enabled:
        raise HTTPException(status_code=409, detail="connector is disabled")
    if connector_checksum(row) != snapshot.get("checksum"):
        raise HTTPException(status_code=409, detail="connector checksum mismatch")

    # Production network transport is intentionally not wired. This deterministic
    # adapter is the only transport in this slice and never reaches the public net.
    adapter = FakeHttpAdapter(
        HttpAdapterResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps({}).encode("utf-8"),
            final_url=connector_url(row),
        )
    )
    try:
        return await execute_frozen_connector(
            row,
            request=payload.request,
            frozen_checksum=snapshot["checksum"],
            adapter=adapter,
        )
    except ConnectorPolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
