"""
Integration fixtures for CI-locked PostgreSQL 16 and Chroma.

These fixtures talk to real services from docker-compose.ci.yml.
They must never fall back to SQLite conclusions for PostgreSQL semantics.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
SERVICE_LOCK_PATH = REPO_ROOT / ".github" / "ci" / "service-lock.json"
COMPOSE_CI_PATH = REPO_ROOT / "docker-compose.ci.yml"
ARTIFACTS_DIR = BACKEND_ROOT / "artifacts"

# Fail-closed defaults match service-lock.json (overridable via env for CI runners).
# Prefer 127.0.0.1 over localhost to avoid Windows IPv6 (::1) connect hangs
# when Docker only publishes IPv4 port mappings.
DEFAULT_PG_ASYNC = (
    "postgresql+asyncpg://novelmind:novelmind@127.0.0.1:5433/novelmind_ci"
)
DEFAULT_PG_SYNC = (
    "postgresql+psycopg2://novelmind:novelmind@127.0.0.1:5433/novelmind_ci"
)
DEFAULT_CHROMA_HOST = "127.0.0.1"
DEFAULT_CHROMA_PORT = 8001


def _load_service_lock() -> dict[str, Any]:
    if not SERVICE_LOCK_PATH.is_file():
        raise pytest.UsageError(
            f"service-lock.json missing (fail closed): {SERVICE_LOCK_PATH}"
        )
    data = json.loads(SERVICE_LOCK_PATH.read_text(encoding="utf-8"))
    pg_digest = (data.get("postgres") or {}).get("digest") or ""
    chroma_digest = (data.get("chroma") or {}).get("digest") or ""
    if not str(pg_digest).startswith("sha256:") or len(str(pg_digest)) < 20:
        raise pytest.UsageError(
            "service-lock postgres.digest missing or invalid (fail closed)"
        )
    if not str(chroma_digest).startswith("sha256:") or len(str(chroma_digest)) < 20:
        raise pytest.UsageError(
            "service-lock chroma.digest missing or invalid (fail closed)"
        )
    if not COMPOSE_CI_PATH.is_file():
        raise pytest.UsageError(
            f"docker-compose.ci.yml missing (fail closed): {COMPOSE_CI_PATH}"
        )
    compose_text = COMPOSE_CI_PATH.read_text(encoding="utf-8")
    pg_hex = str(pg_digest).removeprefix("sha256:")
    chroma_hex = str(chroma_digest).removeprefix("sha256:")
    if pg_hex not in compose_text:
        raise pytest.UsageError(
            "postgres digest drift: service-lock.json does not match docker-compose.ci.yml"
        )
    if chroma_hex not in compose_text:
        raise pytest.UsageError(
            "chroma digest drift: service-lock.json does not match docker-compose.ci.yml"
        )
    return data


@pytest.fixture(scope="session")
def service_lock() -> dict[str, Any]:
    """Pinned service lock manifest (digest-present fail closed)."""
    return _load_service_lock()


@pytest.fixture(scope="session")
def pg_async_url(service_lock: dict[str, Any]) -> str:
    return os.environ.get(
        "NOVELMIND_CI_DATABASE_URL",
        service_lock["postgres"].get("async_url", DEFAULT_PG_ASYNC),
    )


@pytest.fixture(scope="session")
def pg_sync_url(service_lock: dict[str, Any]) -> str:
    return os.environ.get(
        "NOVELMIND_CI_DATABASE_SYNC_URL",
        service_lock["postgres"].get("sync_url", DEFAULT_PG_SYNC),
    )


@pytest.fixture(scope="session")
def chroma_host(service_lock: dict[str, Any]) -> str:
    return os.environ.get(
        "NOVELMIND_CI_CHROMA_HOST",
        DEFAULT_CHROMA_HOST,
    )


@pytest.fixture(scope="session")
def chroma_port(service_lock: dict[str, Any]) -> int:
    raw = os.environ.get("NOVELMIND_CI_CHROMA_PORT")
    if raw:
        return int(raw)
    return int(service_lock["chroma"].get("host_port", DEFAULT_CHROMA_PORT))


@pytest.fixture(scope="session")
def chroma_health_url(
    service_lock: dict[str, Any], chroma_host: str, chroma_port: int
) -> str:
    path = service_lock["chroma"].get("health_path", "/api/v2/heartbeat")
    return f"http://{chroma_host}:{chroma_port}{path}"


@pytest.fixture(scope="session")
def artifacts_dir() -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS_DIR


@pytest.fixture(scope="session")
def require_postgres(pg_sync_url: str) -> None:
    """Skip session only if Postgres is unreachable (blocked_dependency)."""
    try:
        engine = create_engine(pg_sync_url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception as exc:  # pragma: no cover - infra path
        pytest.skip(f"blocked_dependency: PostgreSQL 16 CI service unavailable: {exc}")


@pytest.fixture(scope="session")
def require_chroma(chroma_health_url: str) -> None:
    """Skip session only if Chroma heartbeat fails (blocked_dependency)."""
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(chroma_health_url)
            resp.raise_for_status()
    except Exception as exc:  # pragma: no cover - infra path
        pytest.skip(f"blocked_dependency: Chroma CI service unavailable: {exc}")


def reset_public_schema(sync_url: str) -> None:
    """Drop and recreate public schema so migrations start from empty DB."""
    engine = create_engine(sync_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
    engine.dispose()


def run_alembic(
    *args: str,
    database_url: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run alembic CLI against the CI database (sync driver for Alembic)."""
    env = os.environ.copy()
    # Settings.env_prefix = NOVELMIND_; alembic env.py reads settings.database_url
    # and rewrites +asyncpg → +psycopg2, so either form is fine.
    env["NOVELMIND_DATABASE_URL"] = database_url.replace("+psycopg2", "+asyncpg")
    # Keep debug true so production secret validators do not block CI migrations.
    # Explicit secrets still satisfy length requirements if a parent sets debug=false.
    env["NOVELMIND_DEBUG"] = "true"
    env.setdefault(
        "NOVELMIND_SECRET_KEY",
        "ci-only-integration-secret-key-32chars-min",
    )
    env.setdefault(
        "NOVELMIND_ENCRYPTION_KEY",
        "ci-only-integration-encryption-key-32c",
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.fixture
def empty_postgres(pg_sync_url: str, require_postgres: None) -> Iterator[str]:
    """Provide a freshly emptied PostgreSQL database for migration tests."""
    reset_public_schema(pg_sync_url)
    yield pg_sync_url


@pytest_asyncio.fixture
async def pg_engine(pg_async_url: str, require_postgres: None):
    """Async engine bound to CI PostgreSQL (schema already migrated by caller)."""
    engine = create_async_engine(pg_async_url, echo=False, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine) -> AsyncSession:
    factory = async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def vector_store_ci(chroma_host: str, chroma_port: int, require_chroma: None):
    """VectorStore pointed at CI Chroma (not the default localhost:8001)."""
    from app.services.vector_store import VectorStore

    return VectorStore(host=chroma_host, port=chroma_port)


def fixed_embedding(seed: int = 1, dims: int = 8) -> list[float]:
    """Deterministic fixed vector for store contract only (not semantic quality)."""
    base = [(seed * (i + 1) % 17) / 17.0 for i in range(dims)]
    norm = sum(x * x for x in base) ** 0.5 or 1.0
    return [x / norm for x in base]


DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
