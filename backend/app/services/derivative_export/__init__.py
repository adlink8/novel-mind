"""Derivative-only reproducible Markdown/EPUB export (Phase 39-01).

D-39-01/D-39-02: one frozen ``ExportSnapshot`` consumed by two deterministic
serializers (Markdown, EPUB3) — no third-party EPUB dependency and no
independent live DB reads inside the serializers.
"""

from app.services.derivative_export.epub import render_epub
from app.services.derivative_export.manifest import (
    derivative_export_manifest_hash,
    seal_derivative_export_manifest,
)
from app.services.derivative_export.markdown import render_markdown
from app.services.derivative_export.snapshot import (
    ExportSnapshot,
    ExportSnapshotError,
    ExportSnapshotService,
    FrozenDerivativeExport,
    seal_export_snapshot,
)

__all__ = [
    "ExportSnapshot",
    "ExportSnapshotError",
    "ExportSnapshotService",
    "FrozenDerivativeExport",
    "derivative_export_manifest_hash",
    "render_epub",
    "render_markdown",
    "seal_derivative_export_manifest",
    "seal_export_snapshot",
]
