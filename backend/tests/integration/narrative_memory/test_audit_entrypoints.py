from __future__ import annotations

import json

import pytest

from app.core.database import get_db
from app.core.security import require_user
from app.main import app
from app.services.narrative_memory.audit_contracts import EligibilityReport
from scripts.run_asset_audit import (
    canonical_report_json,
    collect_report,
    report_exit_code,
)
from tests.integration.narrative_memory.test_audit_pg import _seed_valid_hierarchy

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_admin_api_and_cli_return_same_canonical_report(
    asgi_client, audit_pg_session
):
    owner, novel, _ = await _seed_valid_hierarchy(audit_pg_session)
    owner.is_superuser = True
    await audit_pg_session.flush()

    async def override_db():
        yield audit_pg_session

    async def as_admin():
        return owner

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_user] = as_admin

    response = await asgi_client.get(
        f"/api/admin/asset-audit/{novel.id}", params={"owner_id": owner.id}
    )
    assert response.status_code == 200
    api_report = EligibilityReport.model_validate(response.json())

    cli_report = await collect_report(
        owner_id=owner.id, novel_id=novel.id, session=audit_pg_session
    )
    assert canonical_report_json(api_report) == canonical_report_json(cli_report)
    assert json.loads(canonical_report_json(cli_report)) == cli_report.model_dump(
        mode="json"
    )
    assert report_exit_code(cli_report) == 0


@pytest.mark.asyncio
async def test_non_admin_is_rejected_before_inventory(asgi_client, audit_pg_session):
    owner, novel, _ = await _seed_valid_hierarchy(audit_pg_session)

    async def override_db():
        yield audit_pg_session

    async def as_non_admin():
        return owner

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_user] = as_non_admin
    response = await asgi_client.get(
        f"/api/admin/asset-audit/{novel.id}", params={"owner_id": owner.id}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_blocked_cli_report_has_deterministic_nonzero_exit(audit_pg_session):
    report = await collect_report(owner_id=999, novel_id=999, session=audit_pg_session)
    first = canonical_report_json(report)
    second = canonical_report_json(
        await collect_report(owner_id=999, novel_id=999, session=audit_pg_session)
    )
    assert first == second
    assert report_exit_code(report) == 2
