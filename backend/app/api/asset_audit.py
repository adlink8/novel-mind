"""Administrator-only, read-only narrative-memory asset audit endpoint."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models.user import User
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_contracts import EligibilityReport
from app.services.narrative_memory.audit_pg import PostgresAuditSource

router = APIRouter(prefix="/api/admin/asset-audit", tags=["资产资格审计"])


@router.get("/{novel_id}", response_model=EligibilityReport)
async def get_asset_audit(
    novel_id: int,
    owner_id: int = Query(ge=1),
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> EligibilityReport:
    """Reproduce a scoped eligibility report without repairing or starting work."""

    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可执行资产资格审计",
        )
    return await audit_assets(
        PostgresAuditSource(db), owner_id=owner_id, novel_id=novel_id
    )
