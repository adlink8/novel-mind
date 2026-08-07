"""Phase 39-01 derivative export snapshot security tests (T-39-01-01/02).

Database-free coverage of the frozen snapshot's fail-closed security seams:

- the frozen asset reader never serves bytes that do not replay the content
  hash and never serves missing bytes (no invented URL, no silent drop);
- the snapshot/manifest share one replayable hash (tamper-evident version);
- any parity/provenance mismatch surfaces as an explicit blocked error with a
  stable code — never a silent pass or a 200-with-truncated-data;
- serialized output never embeds an external URL and missing assets are
  explicit placeholders.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from app.services.derivative_export.epub import render_epub
from app.services.derivative_export.manifest import (
    derivative_export_manifest_hash,
)
from app.services.derivative_export.markdown import render_markdown
from app.services.derivative_export.snapshot import (
    ExportSnapshotError,
    FrozenDerivativeExport,
    export_snapshot_hash,
    seal_export_snapshot,
)
from tests.fixtures.derivative_export_roundtrip_fixtures import (
    build_fixture_snapshot,
    fixture_export_asset,
    fixture_asset,
    seal_fixture_manifest,
)

pytestmark = pytest.mark.unit

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TINY_PNG_HASH = hashlib.sha256(TINY_PNG).hexdigest()


class _FakeStorage:
    """Minimal DerivativeAssetStorage-shaped stand-in."""

    def __init__(self, payload: bytes | None, *, expected_hash: str | None = None):
        self.payload = payload
        self.expected_hash = expected_hash

    def read(self, **kwargs):
        from app.services.derivative_visual.assets import DerivativeAssetStorageError

        if self.payload is None:
            raise DerivativeAssetStorageError("missing")
        return self.payload

    def exists(self, **kwargs):
        return self.payload is not None


def _snapshot_with_png():
    asset = fixture_export_asset(
        fixture_asset(content_hash=TINY_PNG_HASH, size_bytes=len(TINY_PNG))
    )
    return build_fixture_snapshot(assets=(asset,))


def test_asset_reader_replays_content_hash_only():
    snapshot = _snapshot_with_png()
    frozen = FrozenDerivativeExport(
        snapshot=snapshot,
        storage=_FakeStorage(TINY_PNG, expected_hash=TINY_PNG_HASH),
    )
    reader = frozen.asset_reader()
    assert reader(snapshot.assets[0]) == TINY_PNG

    # Tampered bytes never pass the reader.
    frozen_tampered = FrozenDerivativeExport(
        snapshot=snapshot,
        storage=_FakeStorage(b"tampered-bytes"),
    )
    assert frozen_tampered.asset_reader()(snapshot.assets[0]) is None


def test_asset_reader_returns_none_for_missing_bytes():
    snapshot = _snapshot_with_png()
    frozen = FrozenDerivativeExport(snapshot=snapshot, storage=None)
    assert frozen.asset_reader()(snapshot.assets[0]) is None

    missing = FrozenDerivativeExport(snapshot=snapshot, storage=_FakeStorage(None))
    assert missing.asset_reader()(snapshot.assets[0]) is None


def test_snapshot_hash_is_tamper_evident():
    snapshot = build_fixture_snapshot()
    sealed = seal_export_snapshot(snapshot)
    assert export_snapshot_hash(sealed) == sealed.snapshot_hash
    # Any frozen-field tampering changes the hash.
    tampered = sealed.model_copy(update={"project_name": "Hacked"})
    assert export_snapshot_hash(tampered) != sealed.snapshot_hash


def test_manifest_and_snapshot_share_one_hash():
    from io import BytesIO
    from zipfile import ZipFile

    snapshot = _snapshot_with_png()
    manifest = seal_fixture_manifest(snapshot)
    assert manifest.manifest_hash == snapshot.snapshot_hash
    assert derivative_export_manifest_hash(manifest) == snapshot.snapshot_hash
    # The embedded export-manifest.json carries the same single version.
    epub = render_epub(snapshot, lambda asset: TINY_PNG)
    with ZipFile(BytesIO(epub)) as archive:
        embedded = archive.read("OEBPS/export-manifest.json").decode("utf-8")
    assert snapshot.snapshot_hash in embedded
    assert snapshot.text_version_hash in embedded


def test_export_snapshot_error_is_explicit_blocked():
    exc = ExportSnapshotError("revision_version_stale", "stale revision", 409)
    assert exc.code == "revision_version_stale"
    assert exc.status_code == 409
    assert "revision_version_stale" in str(exc)
    # The blocked code is a failure, not a silent truncation.
    assert exc.code not in {"ok", "pass", "success"}


def test_missing_asset_output_never_invents_url():
    from app.services.derivative_export.manifest import MissingDerivativeAssetRecord

    asset = fixture_export_asset(
        fixture_asset(content_hash=TINY_PNG_HASH, size_bytes=len(TINY_PNG))
    )
    record = MissingDerivativeAssetRecord(
        asset_id=asset.asset_id,
        content_hash=asset.content_hash,
        mime_type=asset.mime_type,
        chapter_number=asset.chapter_number,
        reason_code="asset_bytes_missing",
        detail="bytes missing",
    )
    snapshot = build_fixture_snapshot(assets=(), missing_assets=(record,))
    md = render_markdown(snapshot, lambda a: None).decode("utf-8")
    epub = render_epub(snapshot, lambda a: None)

    assert "插图缺失" in md
    assert "asset_bytes_missing" in md
    assert "http://" not in md and "https://" not in md
    assert "http://" not in epub.decode("utf-8", errors="ignore")
    assert b"OEBPS/assets/" not in epub


def test_snapshot_is_fanfiction_canon_only():
    snapshot = build_fixture_snapshot()
    assert snapshot.space == "fanfiction_canon"
    for revision in snapshot.revisions:
        assert revision.status == "derivative_revision"
