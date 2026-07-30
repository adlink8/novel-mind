"""启动时 embedding 断点恢复测试。"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from app import main


@pytest.mark.asyncio
async def test_startup_scans_pending_novels_and_resumes() -> None:
    db = AsyncMock()

    pending_result = MagicMock()
    pending_result.scalars.return_value.all.return_value = [91]

    job = MagicMock()
    job.id = 7
    job.status = "embedding"
    job_result = MagicMock()
    job_result.scalars.return_value.first.return_value = job

    db.execute.side_effect = [pending_result, job_result]

    @asynccontextmanager
    async def session_factory():
        yield db

    resume = AsyncMock(
        return_value={
            "novel_id": 91,
            "total_chunks": 1945,
            "embedded_chunks": 1945,
            "failed_chunks": 0,
            "status": "ready",
        }
    )

    with (
        patch("app.core.database.async_session_factory", session_factory),
        patch("app.services.indexing_service.indexing_service.resume_pending_embeddings", resume),
    ):
        await main._resume_pending_embeddings_on_startup()

    resume.assert_awaited_once_with(db, 91)
    assert job.status == "ready"
    assert job.progress == 100
    assert "自动恢复索引完成" in job.message
    db.commit.assert_awaited_once()
