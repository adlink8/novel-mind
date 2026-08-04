"""Phase 39-02 package manifest + audit contract unit tests (D-39-03).

Pure, database-free coverage of:

- package manifest parity — ``package_hash`` replays over every content entry
  + metadata; the same snapshot always yields the same package bytes and the
  same package hash;
- tamper detection — a mutated content entry no longer matches the recorded
  per-entry hash, and a mutated entry list breaks the sealed package hash;
- blocked-reason replay — ``quality_qualification_blocked_reason`` is derived
  deterministically from the manifest hash and replays;
- the three-dimension audit contract — implementation_readiness /
  sample_data_coverage / quality_qualification stay independent, the quality
  dimension reflects the real Phase 22 state (0/3 -> blocked, verdict blocked)
  and a falsely-green report cannot be constructed;
- no promotion / pointer / planning-state capability.
"""

from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.services.derivative_export import audit as audit_module
from app.services.derivative_export import package as package_module
from app.services.derivative_export.audit import (
    DerivativeExportAuditDimension,
    DerivativeExportAuditDimensionKind,
    DerivativeExportAuditEvidence,
    DerivativeExportAuditReport,
    DerivativeExportAuditStatus,
    DerivativeExportPhase22Evidence,
    audit_derivative_export_has_promotion_capability,
    audit_derivative_export_mutates_planning_state,
    audit_report_hash,
    build_derivative_export_audit,
    quality_qualification_blocked_reason,
    replay_quality_qualification_blocked_reason,
)
from app.services.derivative_export.package import (
    DERIVATIVE_EXPORT_PACKAGE_SCHEMA_VERSION,
    build_derivative_export_package,
    derivative_export_package_hash,
)
from tests.fixtures.derivative_export_roundtrip_fixtures import (
    HEX64_F,
    OWNER_ID,
    build_fixture_snapshot,
    fixture_asset,
    fixture_chapter,
    fixture_export_asset,
    fixture_export_revision,
    seal_fixture_manifest,
)

pytestmark = pytest.mark.unit

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TINY_PNG_HASH = hashlib.sha256(TINY_PNG).hexdigest()


class _TinyReader:
    """Serves the tiny PNG only when the content hash actually replays it."""

    def __call__(self, asset):
        if asset.content_hash != TINY_PNG_HASH:
            return None
        return TINY_PNG


def _readable_snapshot():
    asset = fixture_export_asset(
        fixture_asset(content_hash=TINY_PNG_HASH, size_bytes=len(TINY_PNG))
    )
    return build_fixture_snapshot(assets=(asset,))


def _phase22(green_observed: int = 0) -> DerivativeExportPhase22Evidence:
    return DerivativeExportPhase22Evidence(
        green_observed=green_observed,
        source=".planning/STATE.md",
        source_hash="a" * 64,
    )


def _audit_evidence(kind: str = "roundtrip_fixtures_present") -> tuple[DerivativeExportAuditEvidence, ...]:
    return (
        DerivativeExportAuditEvidence(
            kind=kind, location="backend/tests", detail="fixture evidence present"
        ),
    )


# ---------------------------------------------------------------------------
# Package manifest parity (D-39-03: hash covers every content entry + metadata)
# ---------------------------------------------------------------------------


def test_package_manifest_hash_replays_and_is_deterministic():
    snapshot = _readable_snapshot()
    payload_a, pkg_a = build_derivative_export_package(snapshot, _TinyReader())
    payload_b, pkg_b = build_derivative_export_package(snapshot, _TinyReader())

    assert len(pkg_a.package_hash) == 64
    assert derivative_export_package_hash(pkg_a) == pkg_a.package_hash
    assert pkg_a.package_hash == pkg_b.package_hash
    assert payload_a == payload_b
    # The package is bound to the frozen manifest (one hash for all).
    assert pkg_a.snapshot_hash == snapshot.snapshot_hash
    assert pkg_a.manifest_schema_version
    assert pkg_a.package_id.startswith(f"derivative-export:{snapshot.project_id}:")
    # Non-guessed artifact id embeds the snapshot hash (not a client value).
    assert pkg_a.package_id.endswith(snapshot.snapshot_hash)


def test_package_manifest_covers_every_content_entry():
    snapshot = _readable_snapshot()
    payload, pkg = build_derivative_export_package(snapshot, _TinyReader())
    with ZipFile(BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert "package-manifest.json" in names
        listed = {entry.name for entry in pkg.entries}
        # Every zip entry except the index itself is a covered content entry.
        assert listed == names - {"package-manifest.json"}
        assert "manifest.json" in listed
        assert "provenance.json" in listed
        assert f"assets/{TINY_PNG_HASH}.png" in listed
        for entry in pkg.entries:
            assert hashlib.sha256(archive.read(entry.name)).hexdigest() == (
                entry.content_hash
            )
            assert archive.getinfo(entry.name).file_size == entry.size_bytes
        provenance = json.loads(archive.read("provenance.json"))
    # Provenance carries asset ids/hashes/source refs + citation leaf hashes.
    assert provenance["schema_version"].startswith("derivative-export-provenance")
    assert provenance["assets"][0]["asset_id"]
    assert provenance["assets"][0]["content_hash"] == TINY_PNG_HASH
    assert provenance["assets"][0]["source_refs"][0]["source_asset_id"]
    assert provenance["citations"][0]["citation_hash"]
    # Owner isolation evidence is present and honest.
    assert provenance["owner_isolation"]["space_allowed"] is True
    assert provenance["owner_isolation"]["revisions_owner_scoped"] is True
    assert provenance["owner_isolation"]["assets_derivative_namespace"] is True


def test_package_deterministic_when_snapshot_content_changes_hash_changes():
    a = _readable_snapshot()
    chapter_b = build_fixture_snapshot(
        chapters=(fixture_chapter(content="不同的正文。"),),
        revisions=(),
        assets=(),
        citations=(),
    )
    _payload_a, pkg_a = build_derivative_export_package(a, _TinyReader())
    _payload_b, pkg_b = build_derivative_export_package(chapter_b, lambda asset: None)
    assert pkg_a.package_hash != pkg_b.package_hash


def test_tampered_content_entry_is_detected():
    snapshot = _readable_snapshot()
    payload, pkg = build_derivative_export_package(snapshot, _TinyReader())

    def _tampered() -> bytes:
        with ZipFile(BytesIO(payload)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        tampered = entries["manifest.json"]
        tampered = bytes([tampered[0] ^ 0xFF]) + tampered[1:]
        out = BytesIO()
        with ZipFile(out, "w") as zf:
            for name, content in entries.items():
                zf.writestr(name, tampered if name == "manifest.json" else content)
        return out.getvalue()

    with ZipFile(BytesIO(_tampered())) as archive:
        index = json.loads(archive.read("package-manifest.json"))
        recorded = next(
            entry for entry in index["entries"] if entry["name"] == "manifest.json"
        )
        actual = hashlib.sha256(archive.read("manifest.json")).hexdigest()
    # The recorded per-entry hash no longer replays the mutated bytes.
    assert recorded["content_hash"] != actual
    # The sealed server hash still replays the *original* index — the tamper
    # is detected by comparing bytes against the recorded hashes / header.
    assert derivative_export_package_hash(index) == index["package_hash"]


def test_hash_mutation_of_entries_breaks_package_hash():
    snapshot = _readable_snapshot()
    _payload, pkg = build_derivative_export_package(snapshot, _TinyReader())
    data = pkg.model_dump(mode="json")
    # Mutate the recorded content hash of the manifest entry.
    data["entries"][0] = {
        **data["entries"][0],
        "content_hash": "1" * 64,
    }
    assert derivative_export_package_hash(data) != pkg.package_hash
    # And a mutation of an entry name is detected the same way.
    data = pkg.model_dump(mode="json")
    data["entries"][0] = {**data["entries"][0], "name": "tampered.json"}
    assert derivative_export_package_hash(data) != pkg.package_hash


def test_package_imports_only_stdlib_and_app():
    """T-39-02-SC: the archive writer must not add a third-party package.

    ``pydantic`` is the pre-existing project data layer (used by every export
    contract); the zip writer itself is stdlib only (``zipfile``/``hashlib``).
    """
    import ast

    source = open(package_module.__file__, encoding="utf-8").read()
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    third_party = [
        name
        for name in imports
        if name
        and not name.startswith("app.")
        and name
        not in {
            "zipfile",
            "json",
            "io",
            "typing",
            "__future__",
            "hashlib",
            "pydantic",
        }
    ]
    assert third_party == [], (
        f"package module must be stdlib-only; found {third_party}"
    )


# ---------------------------------------------------------------------------
# Blocked reason replay (D-39-03: derive from the manifest, replayable)
# ---------------------------------------------------------------------------


def test_quality_blocked_reason_is_manifest_bound_and_replays():
    snapshot = _readable_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    reason = quality_qualification_blocked_reason(
        snapshot_hash=manifest.manifest_hash, green_observed=0
    )
    assert reason == replay_quality_qualification_blocked_reason(
        snapshot_hash=manifest.manifest_hash, green_observed=0
    )
    # A different manifest hash derives a different reason.
    other = quality_qualification_blocked_reason(
        snapshot_hash="2" * 64, green_observed=0
    )
    assert reason != other


def test_audit_blocked_reason_replays_from_the_report():
    snapshot = _readable_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    report = build_derivative_export_audit(
        manifest=manifest,
        phase22=_phase22(0),
        implementation_evidence=_audit_evidence("package_buildable"),
        sample_data_evidence=_audit_evidence("roundtrip_fixtures_present"),
    )
    reason = report.dimensions[2].blocked_reasons[0]
    assert reason == replay_quality_qualification_blocked_reason(
        snapshot_hash=manifest.manifest_hash, green_observed=0
    )
    assert report.blocked_reasons == tuple(sorted(report.blocked_reasons))
    assert report.blocked_reasons == (reason,)


# ---------------------------------------------------------------------------
# Three-dimension audit contract (independent dimensions, fail-closed)
# ---------------------------------------------------------------------------


def test_audit_three_dimensions_are_independent_and_ordered():
    snapshot = _readable_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    report = build_derivative_export_audit(
        manifest=manifest,
        phase22=_phase22(0),
        implementation_evidence=_audit_evidence("package_buildable"),
        sample_data_evidence=_audit_evidence("roundtrip_fixtures_present"),
    )
    kinds = [d.dimension for d in report.dimensions]
    assert kinds == [
        DerivativeExportAuditDimensionKind.IMPLEMENTATION_READINESS,
        DerivativeExportAuditDimensionKind.SAMPLE_DATA_COVERAGE,
        DerivativeExportAuditDimensionKind.QUALITY_QUALIFICATION,
    ]
    # Implementation + sample data verified on evidence; quality is real-state.
    assert report.dimensions[0].status == DerivativeExportAuditStatus.VERIFIED
    assert report.dimensions[1].status == DerivativeExportAuditStatus.VERIFIED
    assert report.dimensions[2].status == DerivativeExportAuditStatus.BLOCKED
    assert report.verdict == "blocked"
    assert report.blocked_reasons == (report.dimensions[2].blocked_reasons[0],)
    # The report hash replays and no single completion percentage exists.
    assert report.report_hash == audit_report_hash(report)
    assert report.has_completion_percentage is False


def test_audit_missing_evidence_fails_closed_per_dimension():
    snapshot = _readable_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    report = build_derivative_export_audit(
        manifest=manifest,
        phase22=_phase22(0),
        implementation_evidence=(),
        sample_data_evidence=(),
        package_buildable=False,
        package_manifest_hash_parity=False,
        sample_data_present=False,
    )
    assert report.dimensions[0].status == DerivativeExportAuditStatus.BLOCKED
    assert "implementation_evidence_missing" in report.dimensions[0].blocked_reasons
    assert "package_build_failed" in report.dimensions[0].blocked_reasons
    assert "package_manifest_hash_parity_failed" in report.dimensions[0].blocked_reasons
    assert report.dimensions[1].status == DerivativeExportAuditStatus.BLOCKED
    assert "sample_data_evidence_missing" in report.dimensions[1].blocked_reasons
    assert "sample_data_fixtures_missing" in report.dimensions[1].blocked_reasons
    assert report.verdict == "blocked"


def test_audit_quality_dimension_cannot_be_forced_green():
    """Falsely-green audit: a green quality claim with Phase 22 blocked fails."""
    snapshot = _readable_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    report = build_derivative_export_audit(
        manifest=manifest,
        phase22=_phase22(0),
        implementation_evidence=_audit_evidence("package_buildable"),
        sample_data_evidence=_audit_evidence("roundtrip_fixtures_present"),
    )
    data = report.model_dump(mode="json")
    data["dimensions"][2]["status"] = "verified"
    data["dimensions"][2]["blocked_reasons"] = []
    data["verdict"] = "qualified_candidate"
    data["blocked_reasons"] = []
    with pytest.raises(ValueError) as exc:
        DerivativeExportAuditReport.model_validate(data)
    assert "quality_qualification" in str(exc.value)


def test_audit_direct_report_requires_exactly_three_dimensions():
    snapshot = _readable_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    report = build_derivative_export_audit(
        manifest=manifest, phase22=_phase22(0)
    )
    data = report.model_dump(mode="json")
    data["dimensions"] = data["dimensions"][:2]
    with pytest.raises(ValueError):
        DerivativeExportAuditReport.model_validate(data)


def test_audit_has_no_promotion_or_pointer_capability():
    assert audit_derivative_export_has_promotion_capability() is False
    assert audit_derivative_export_mutates_planning_state() is False
    # The module owns no write surface to STATE/ROADMAP or an active pointer.
    assert "def audit_derivative_export_mutates_planning_state" in open(
        audit_module.__file__, encoding="utf-8"
    ).read()


def test_audit_phase22_evidence_is_bound_and_source_hashed():
    phase22 = _phase22(0)
    assert phase22.green_required == 3
    assert phase22.source_hash == "a" * 64
    # Green claim must be impossible while the bound source says blocked: the
    # derivation only uses observed evidence.
    assert phase22.green_observed < phase22.green_required


def test_audit_full_green_only_when_evidence_and_phase22_allow():
    snapshot = _readable_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    report = build_derivative_export_audit(
        manifest=manifest,
        phase22=_phase22(3),
        implementation_evidence=_audit_evidence("package_buildable"),
        sample_data_evidence=_audit_evidence("roundtrip_fixtures_present"),
    )
    assert report.dimensions[0].status == DerivativeExportAuditStatus.VERIFIED
    assert report.dimensions[1].status == DerivativeExportAuditStatus.VERIFIED
    assert report.dimensions[2].status == DerivativeExportAuditStatus.VERIFIED
    assert report.verdict == "qualified_candidate"
    assert report.blocked_reasons == ()


def test_build_derivative_export_audit_accepts_snapshot_or_manifest():
    snapshot = _readable_snapshot()
    from_snapshot = build_derivative_export_audit(
        manifest=snapshot, phase22=_phase22(0)
    )
    manifest = seal_fixture_manifest(snapshot)
    from_manifest = build_derivative_export_audit(
        manifest=manifest, phase22=_phase22(0)
    )
    assert from_snapshot.snapshot_hash == snapshot.snapshot_hash
    assert from_manifest.snapshot_hash == manifest.manifest_hash
    assert from_snapshot.snapshot_hash == from_manifest.snapshot_hash


def test_package_schema_version_constant():
    assert DERIVATIVE_EXPORT_PACKAGE_SCHEMA_VERSION == "derivative-export-package.v1"
