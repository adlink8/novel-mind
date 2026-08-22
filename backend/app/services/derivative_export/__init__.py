"""Derivative-only reproducible export (Phase 39-01/39-02).

D-39-01/D-39-02: one frozen ``ExportSnapshot`` consumed by two deterministic
serializers (Markdown, EPUB3) — no third-party EPUB dependency and no
independent live DB reads inside the serializers.

D-39-03 (Phase 39-02): the bounded provenance ``package`` (asset provenance +
citation package + owner isolation evidence + package-manifest hash over every
entry) and the three-dimension ``audit`` contract (implementation_readiness /
sample_data_coverage / quality_qualification; quality reflects the real
Phase 22 state and cannot be substituted by a Phase 39 pass).
"""

from app.services.derivative_export.audit import (
    DerivativeExportAuditReport,
    build_derivative_export_audit,
)
from app.services.derivative_export.epub import render_epub
from app.services.derivative_export.manifest import (
    derivative_export_manifest_hash,
    seal_derivative_export_manifest,
)
from app.services.derivative_export.markdown import render_markdown
from app.services.derivative_export.package import (
    DerivativeExportPackageManifest,
    build_derivative_export_package,
    derivative_export_package_hash,
)
from app.services.derivative_export.snapshot import (
    ExportSnapshot,
    ExportSnapshotError,
    ExportSnapshotService,
    FrozenDerivativeExport,
    seal_export_snapshot,
)

__all__ = [
    "DerivativeExportAuditReport",
    "DerivativeExportPackageManifest",
    "ExportSnapshot",
    "ExportSnapshotError",
    "ExportSnapshotService",
    "FrozenDerivativeExport",
    "build_derivative_export_audit",
    "build_derivative_export_package",
    "derivative_export_manifest_hash",
    "derivative_export_package_hash",
    "render_epub",
    "render_markdown",
    "seal_derivative_export_manifest",
    "seal_export_snapshot",
]
