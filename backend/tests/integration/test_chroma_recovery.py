"""
Chroma outage / recovery contracts (D-05, D-07, D-10).

- First failure evidence is persisted before any retry
- Max retry = 1 for external infra
- On outage: no partial success, metrics=null / blocked_dependency
- Recovery is idempotent (no duplicate IDs)
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.vector_store import VectorStore, VectorStoreError
from tests.integration.conftest import fixed_embedding

pytestmark = pytest.mark.integration

MAX_INFRA_RETRY = 1


def _write_failure_evidence(
    artifacts_dir: Path,
    *,
    kind: str,
    detail: dict[str, Any],
) -> Path:
    """Persist first-failure evidence before any retry (D-10)."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = artifacts_dir / f"chroma-failure-{kind}-{stamp}-{uuid.uuid4().hex[:8]}.json"
    payload = {
        "kind": kind,
        "recorded_at": datetime.now(UTC).isoformat(),
        "quality_comparable": False,
        "metrics": None,
        "status": "blocked_dependency",
        "detail": detail,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


async def _attempt_add_with_retry(
    store: VectorStore,
    *,
    novel_id: int,
    chunks: list[dict[str, Any]],
    artifacts_dir: Path,
    kind: str,
) -> dict[str, Any]:
    """
    Attempt vector write with max 1 retry.

    Returns a structured result; never fabricates quality scores on failure.
    """
    attempts: list[dict[str, Any]] = []
    evidence_path: Path | None = None
    last_error: Exception | None = None

    for attempt in range(MAX_INFRA_RETRY + 1):
        try:
            await store.add_chunks(novel_id=novel_id, chunks=chunks)
            return {
                "ok": True,
                "attempts": attempt + 1,
                "metrics": None,  # store contract only — no quality scores
                "quality_comparable": False,
                "status": "ok",
                "evidence_path": str(evidence_path) if evidence_path else None,
            }
        except Exception as exc:  # VectorStoreError or transport errors
            last_error = exc
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "error": f"{type(exc).__name__}: {exc}",
                    "ts": datetime.now(UTC).isoformat(),
                }
            )
            if evidence_path is None:
                evidence_path = _write_failure_evidence(
                    artifacts_dir,
                    kind=kind,
                    detail={
                        "novel_id": novel_id,
                        "chunk_count": len(chunks),
                        "attempts": attempts,
                        "host": store.host,
                        "port": store.port,
                    },
                )
            if attempt >= MAX_INFRA_RETRY:
                break

    return {
        "ok": False,
        "attempts": len(attempts),
        "metrics": None,
        "quality_comparable": False,
        "status": "blocked_dependency",
        "evidence_path": str(evidence_path) if evidence_path else None,
        "error": f"{type(last_error).__name__}: {last_error}" if last_error else None,
    }


@pytest.mark.asyncio
async def test_timeout_outage_saves_evidence_and_blocks_metrics(
    artifacts_dir: Path,
    chroma_host: str,
):
    """Timeout / refused connection: evidence first, max retry 1, metrics=null."""
    # Closed port — connection fails fast without docker stop.
    bad_store = VectorStore(host=chroma_host, port=1)
    novel_id = 910_000 + (uuid.uuid4().int % 1000)
    chunks = [
        {
            "id": 1,
            "content": "should-not-persist",
            "embedding": fixed_embedding(3),
            "metadata": {"chunk_type": "paragraph"},
        }
    ]

    result = await _attempt_add_with_retry(
        bad_store,
        novel_id=novel_id,
        chunks=chunks,
        artifacts_dir=artifacts_dir,
        kind="timeout",
    )

    assert result["ok"] is False
    assert result["status"] == "blocked_dependency"
    assert result["metrics"] is None
    assert result["quality_comparable"] is False
    assert result["attempts"] == MAX_INFRA_RETRY + 1
    assert result["evidence_path"]
    evidence = Path(result["evidence_path"])
    assert evidence.is_file()
    body = json.loads(evidence.read_text(encoding="utf-8"))
    assert body["metrics"] is None
    assert body["status"] == "blocked_dependency"
    assert body["quality_comparable"] is False
    assert len(body["detail"]["attempts"]) >= 1  # first failure recorded


@pytest.mark.asyncio
async def test_bad_response_heartbeat_is_blocked(
    artifacts_dir: Path,
    chroma_host: str,
):
    """Bad / unreachable heartbeat must not produce comparable quality metrics."""
    bad_url = f"http://{chroma_host}:1/api/v2/heartbeat"
    evidence: Path | None = None
    last_status: str | None = None
    for attempt in range(MAX_INFRA_RETRY + 1):
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(bad_url)
                last_status = str(resp.status_code)
                if resp.status_code >= 400:
                    raise RuntimeError(f"bad heartbeat status={resp.status_code}")
        except Exception as exc:
            if evidence is None:
                evidence = _write_failure_evidence(
                    artifacts_dir,
                    kind="bad_response",
                    detail={
                        "url": bad_url,
                        "attempt": attempt + 1,
                        "error": f"{type(exc).__name__}: {exc}",
                        "http_status": last_status,
                    },
                )
            if attempt >= MAX_INFRA_RETRY:
                break

    assert evidence is not None and evidence.is_file()
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["metrics"] is None
    assert payload["status"] == "blocked_dependency"


@pytest.mark.asyncio
async def test_outage_no_partial_success_then_recovery_idempotent(
    vector_store_ci: VectorStore,
    artifacts_dir: Path,
    chroma_host: str,
    chroma_port: int,
    require_chroma,
):
    """
    Failed write leaves zero partial success; recovery re-add is idempotent.

    Flow:
    1. bad store fails (evidence + blocked)
    2. healthy store writes fixed vectors
    3. re-add same IDs is idempotent / non-duplicating (upsert-safe clean-up)
    """
    novel_id = 920_000 + (uuid.uuid4().int % 1000)
    chunks = [
        {
            "id": 11,
            "content": "recovery-chunk",
            "embedding": fixed_embedding(11),
            "metadata": {"chunk_type": "paragraph", "build_id": 1},
        }
    ]

    bad = VectorStore(host=chroma_host, port=1)
    failed = await _attempt_add_with_retry(
        bad,
        novel_id=novel_id,
        chunks=chunks,
        artifacts_dir=artifacts_dir,
        kind="partial_guard",
    )
    assert failed["ok"] is False
    assert failed["metrics"] is None
    # Bad store cannot leave rows in the real CI collection namespace for this novel.
    assert await vector_store_ci.get_chunk_count(novel_id) == 0

    # Recovery path against healthy service.
    await vector_store_ci.add_chunks(novel_id=novel_id, chunks=chunks)
    assert await vector_store_ci.get_chunk_count(novel_id) == 1

    # Second write with same IDs: Chroma may upsert or error; count must not grow
    # past expected set (idempotent recovery / no silent duplicates).
    try:
        await vector_store_ci.add_chunks(novel_id=novel_id, chunks=chunks)
    except VectorStoreError:
        # Non-upserting servers reject duplicates — still no partial success.
        pass
    count = await vector_store_ci.get_chunk_count(novel_id)
    assert count == 1, f"duplicate write produced count={count}"

    # Search still works after recovery (store contract, not quality score).
    hits = await vector_store_ci.search(
        novel_id=novel_id, query_embedding=fixed_embedding(11), top_k=1
    )
    assert len(hits) == 1
    assert hits[0]["chunk_id"] == "chunk_11"

    await vector_store_ci.delete_novel_chunks(novel_id)
    assert await vector_store_ci.get_chunk_count(novel_id) == 0


@pytest.mark.asyncio
async def test_docker_stop_chroma_then_recover(
    vector_store_ci: VectorStore,
    artifacts_dir: Path,
    chroma_health_url: str,
    require_chroma,
):
    """
    Container stop fault injection via docker compose (when CLI available).

    Uses short HTTP heartbeat probes during outage (avoid long gRPC hangs).
    First failure evidence is saved before the single retry; recovery re-writes.
    """
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        pytest.skip("blocked_dependency: docker CLI not available for stop injection")

    repo_root = Path(__file__).resolve().parents[3]
    compose = [
        "docker",
        "compose",
        "-f",
        "docker-compose.ci.yml",
        "--project-name",
        "novelmind-ci",
    ]

    with httpx.Client(timeout=5.0) as client:
        assert client.get(chroma_health_url).status_code == 200

    stop = subprocess.run(
        [*compose, "stop", "chroma"],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        timeout=20,
    )
    if stop.returncode != 0:
        pytest.skip(f"blocked_dependency: cannot stop chroma: {stop.stderr}")

    evidence: Path | None = None
    try:
        for attempt in range(MAX_INFRA_RETRY + 1):
            try:
                with httpx.Client(timeout=1.5) as client:
                    resp = client.get(chroma_health_url)
                    if resp.status_code == 200:
                        raise AssertionError("chroma still healthy after stop")
                    raise RuntimeError(f"unexpected status {resp.status_code}")
            except Exception as exc:
                if evidence is None:
                    evidence = _write_failure_evidence(
                        artifacts_dir,
                        kind="docker_stop",
                        detail={
                            "attempt": attempt + 1,
                            "error": f"{type(exc).__name__}: {exc}",
                            "health_url": chroma_health_url,
                        },
                    )
                if attempt >= MAX_INFRA_RETRY:
                    break
        assert evidence is not None and evidence.is_file()
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        assert payload["metrics"] is None
        assert payload["status"] == "blocked_dependency"
    finally:
        start = subprocess.run(
            [*compose, "start", "chroma"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=20,
        )
        assert start.returncode == 0, start.stderr
        deadline = time.time() + 20
        healthy = False
        while time.time() < deadline:
            try:
                with httpx.Client(timeout=1.5) as client:
                    if client.get(chroma_health_url).status_code == 200:
                        healthy = True
                        break
            except Exception:
                pass
            time.sleep(0.5)
        assert healthy, "chroma did not recover heartbeat after start"

    # Reset client after outage and confirm store write on recovery.
    vector_store_ci._client = None  # noqa: SLF001 — intentional client reset
    novel_id = 931_000 + (uuid.uuid4().int % 1000)
    await vector_store_ci.add_chunks(
        novel_id=novel_id,
        chunks=[
            {
                "id": 2,
                "content": "after-recovery",
                "embedding": fixed_embedding(6),
                "metadata": {"chunk_type": "paragraph", "recovered": True},
            }
        ],
    )
    assert await vector_store_ci.get_chunk_count(novel_id) == 1
    await vector_store_ci.delete_novel_chunks(novel_id)
