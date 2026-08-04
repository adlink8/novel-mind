"""Content-hash asset storage with owner containment (Phase 33-02, REQ-VIS-04).

D-33-03 / the RESEARCH "binary path traversal" pitfall: asset bytes are stored
by content hash under an owner/novel-scoped path, the DB row stays authoritative
for MIME/dimensions/rights/approval, and no raw filesystem path is ever exposed
to clients. This module is the local asset store (the ``novel_service.py``
upload-containment analog):

- ``store`` validates owner/novel containment, MIME allowlist, size limit and
  that the content hash replays from the bytes, then writes atomically and
  returns the relative ``storage_key``;
- ``read`` / ``remove`` / ``exists`` resolve only paths inside the
  owner/novel scope (path traversal fails closed);
- ``quarantine`` removes files under an owner/novel scope that are no longer
  referenced by the durable AssetRevision rows.

No secrets, no cover_url reuse and no network object storage in this slice; an
object-storage adapter seam remains a deployment decision (RESEARCH Q3).
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Collection

# Content-hash addressed path: assets/{owner}/{novel}/{hash[:2]}/{hash}{ext}
# The storage_key is the relative path recorded on the immutable AssetRevision.
ASSET_SCOPE_PREFIX = "assets"

# MIME allowlist and size cap (V5 input/MIME validation). An asset revision can
# never be a zero-byte success (D-33-01), so empty payloads are rejected here.
ALLOWED_MIME_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
MAX_ASSET_BYTES = 20 * 1024 * 1024  # 20 MiB worst-case mock/reference asset


class AssetStorageError(ValueError):
    """Fail-closed asset storage gate violation."""


class AssetNotFound(AssetStorageError):
    """The bytes do not exist (or are outside the caller's scope)."""


class AssetStorage:
    """Content-hash asset store under a local root directory."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root = Path(root_dir).resolve()

    # ------------------------------------------------------------------ write

    def store(
        self,
        *,
        owner_id: int,
        novel_id: int,
        payload: bytes,
        mime_type: str,
        bytes_hash: str,
    ) -> str:
        """Validate and persist asset bytes; returns the relative storage_key."""
        self._require_scope(owner_id, novel_id)
        if not payload:
            raise AssetStorageError("cannot store an empty asset (D-33-01)")
        if len(payload) > MAX_ASSET_BYTES:
            raise AssetStorageError(
                f"asset payload exceeds the {MAX_ASSET_BYTES} byte limit"
            )
        extension = ALLOWED_MIME_TYPES.get(mime_type)
        if extension is None:
            raise AssetStorageError(
                f"unsupported asset mime_type {mime_type!r}; allowed: "
                f"{sorted(ALLOWED_MIME_TYPES)}"
            )
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != bytes_hash:
            raise AssetStorageError(
                "asset bytes_hash does not replay from the payload"
            )

        storage_key = (
            f"{ASSET_SCOPE_PREFIX}/{owner_id}/{novel_id}/"
            f"{bytes_hash[:2]}/{bytes_hash}{extension}"
        )
        target = self._resolve(owner_id, novel_id, storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, payload)
        return storage_key

    @staticmethod
    def _atomic_write(target: Path, payload: bytes) -> None:
        """Write via a temp file in the same directory, then atomically move."""
        fd, temp_path = tempfile.mkstemp(
            prefix=".upload-", dir=str(target.parent), suffix=".tmp"
        )
        try:
            with open(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                import os

                os.fsync(handle.fileno())
            temp = Path(temp_path)
            if target.exists():
                target.unlink()
            temp.replace(target)
        except BaseException:
            Path(temp_path).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------ read

    def read(self, *, owner_id: int, novel_id: int, storage_key: str) -> bytes:
        """Read asset bytes for an owner/novel scope; traversal fails closed."""
        target = self._resolve(owner_id, novel_id, storage_key)
        if not target.is_file():
            raise AssetNotFound(
                f"asset {storage_key!r} does not exist in the owner/novel scope"
            )
        return target.read_bytes()

    def exists(self, *, owner_id: int, novel_id: int, storage_key: str) -> bool:
        try:
            return self._resolve(owner_id, novel_id, storage_key).is_file()
        except AssetStorageError:
            return False

    def remove(self, *, owner_id: int, novel_id: int, storage_key: str) -> None:
        """Remove one stored asset; used by quarantine cleanup."""
        target = self._resolve(owner_id, novel_id, storage_key)
        if target.is_file():
            target.unlink()

    # ------------------------------------------------------------- quarantine

    def quarantine(
        self,
        *,
        owner_id: int,
        novel_id: int,
        referenced_keys: Collection[str] = (),
    ) -> list[str]:
        """Remove files under the owner/novel scope not in ``referenced_keys``.

        Returns the removed storage keys. The durable AssetRevision rows are
        the only authority for which bytes are still referenced.
        """
        self._require_scope(owner_id, novel_id)
        scope_dir = self.root / ASSET_SCOPE_PREFIX / str(owner_id) / str(novel_id)
        if not scope_dir.is_dir():
            return []
        referenced = set(referenced_keys)
        removed: list[str] = []
        for path in sorted(scope_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if rel in referenced:
                continue
            path.unlink(missing_ok=True)
            removed.append(rel)
        return removed

    # ------------------------------------------------------------- containment

    def _resolve(self, owner_id: int, novel_id: int, storage_key: str) -> Path:
        """Resolve a storage_key and fail closed on scope/path traversal."""
        self._require_scope(owner_id, novel_id)
        if not isinstance(storage_key, str) or not storage_key:
            raise AssetStorageError("storage_key must be a non-empty string")
        expected_prefix = (
            f"{ASSET_SCOPE_PREFIX}/{owner_id}/{novel_id}/"
        )
        if not storage_key.startswith(expected_prefix):
            raise AssetStorageError(
                "storage_key is outside the owner/novel scope"
            )
        candidate = (self.root / storage_key).resolve()
        scope_root = (self.root / ASSET_SCOPE_PREFIX / str(owner_id) / str(novel_id)).resolve()
        try:
            candidate.relative_to(scope_root)
        except ValueError:
            raise AssetStorageError(
                "storage_key escapes the owner/novel asset scope"
            ) from None
        return candidate

    @staticmethod
    def _require_scope(owner_id: int, novel_id: int) -> None:
        if not isinstance(owner_id, int) or not isinstance(novel_id, int):
            raise AssetStorageError("owner/novel scope must be integers")
        if owner_id <= 0 or novel_id <= 0:
            raise AssetStorageError(
                "owner/novel scope must be explicit positive integers"
            )

    def default_root(self) -> Path:
        return self.root

    @staticmethod
    def default_storage_root() -> Path:
        """Deployment default root; never exposed as a raw path to clients."""
        from app.config import settings

        base = Path(getattr(settings, "storage_dir", None) or "storage")
        return base / "illustration_assets"


# Re-exported exceptions for tests and the worker.
__all__ = [
    "ALLOWED_MIME_TYPES",
    "ASSET_SCOPE_PREFIX",
    "AssetNotFound",
    "AssetStorage",
    "AssetStorageError",
    "MAX_ASSET_BYTES",
]
