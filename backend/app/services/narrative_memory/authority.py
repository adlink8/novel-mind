"""Explicit-version persistence for validated narrative-memory candidates."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_build import ChunkBuild
from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryEdge,
    NarrativeMemoryManifest,
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
    NarrativeMemoryVersion,
)
from app.models.novel import Novel
from app.services.narrative_memory.audit_contracts import (
    AssetKind,
    EligibilityReport,
    EligibilityStatus,
)
from app.services.narrative_memory.contracts import (
    CandidatePackage,
    CandidateVersionSpec,
    ExactSourceLink,
    MemoryClaim,
    MemoryEdge,
    MemoryNode,
    ModelLineage,
    canonical_json,
    claim_checksum,
    edge_checksum,
    model_lineage_checksum,
    node_checksum,
    source_link_checksum,
)


class CandidateAuthorityError(ValueError):
    """Base class for fail-closed candidate persistence errors."""


class EligibilityRejectedError(CandidateAuthorityError):
    pass


class ScopeMismatchError(CandidateAuthorityError):
    pass


class CandidateConflictError(CandidateAuthorityError):
    pass


class CandidateNotFoundError(CandidateAuthorityError):
    pass


@dataclass(frozen=True)
class PersistedCandidate:
    version_id: int
    node_ids: dict[str, int]
    claim_ids: dict[str, int]
    edge_ids: tuple[int, ...]
    source_link_ids: tuple[int, ...]


class CandidateAuthority:
    """Narrow write seam requiring owner, novel, and candidate version scope."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_version(
        self,
        *,
        owner_id: int,
        novel_id: int,
        spec: CandidateVersionSpec,
        eligibility_report: EligibilityReport,
    ) -> NarrativeMemoryVersion:
        self._require_scope(owner_id=owner_id, novel_id=novel_id)
        if not isinstance(spec, CandidateVersionSpec):
            raise TypeError("spec must be a validated CandidateVersionSpec")
        if not isinstance(eligibility_report, EligibilityReport):
            raise TypeError("eligibility_report must be a validated EligibilityReport")
        if (
            eligibility_report.owner_id != owner_id
            or eligibility_report.novel_id != novel_id
        ):
            raise ScopeMismatchError("eligibility report scope does not match request")

        hierarchy = next(
            (
                asset
                for asset in eligibility_report.assets
                if asset.kind == AssetKind.HIERARCHY
            ),
            None,
        )
        if (
            hierarchy is None
            or hierarchy.status != EligibilityStatus.REUSABLE_EXACT
            or hierarchy.version_id is None
        ):
            raise EligibilityRejectedError("hierarchy must be reusable_exact")

        novel = await self._session.scalar(
            select(Novel).where(Novel.id == novel_id, Novel.owner_id == owner_id)
        )
        if novel is None:
            raise ScopeMismatchError("owner does not own novel")
        build = await self._session.scalar(
            select(ChunkBuild).where(
                ChunkBuild.build_id == hierarchy.version_id,
                ChunkBuild.novel_id == novel_id,
            )
        )
        if (
            build is None
            or not build.immutable
            or build.is_candidate
            or build.status not in {"built", "committed"}
        ):
            raise EligibilityRejectedError("hierarchy lineage is no longer exact")

        if spec.parent_version_id is not None:
            parent = await self._version(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=spec.parent_version_id,
            )
            if parent is None:
                raise ScopeMismatchError("parent version is outside candidate scope")

        report_checksum = self._eligibility_checksum(eligibility_report)
        optional_lineage = self._optional_lineage(eligibility_report)
        expected = {
            "source_snapshot_hash": build.source_snapshot_hash,
            "hierarchy_build_id": build.build_id,
            "hierarchy_checksum": build.manifest_checksum,
            "eligibility_policy_version": eligibility_report.policy_version,
            "eligibility_report_checksum": report_checksum,
            "prompt_hash": spec.prompt_hash,
            "schema_hash": spec.schema_hash,
            "model_lineage": spec.model_lineage.model_dump(mode="json"),
            "decoding_hash": spec.decoding_hash,
            "config_hash": spec.config_hash,
            "policy_hash": spec.policy_hash,
            "optional_source_lineage": optional_lineage,
            "parent_version_id": spec.parent_version_id,
        }
        existing = await self._session.scalar(
            select(NarrativeMemoryVersion).where(
                NarrativeMemoryVersion.owner_id == owner_id,
                NarrativeMemoryVersion.novel_id == novel_id,
                NarrativeMemoryVersion.version_key == spec.version_key,
            )
        )
        if existing is not None:
            self._require_identical(existing, expected, label="version")
            return existing

        row = NarrativeMemoryVersion(
            owner_id=owner_id,
            novel_id=novel_id,
            version_key=spec.version_key,
            **expected,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def persist_package(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        package: CandidatePackage,
    ) -> PersistedCandidate:
        self._require_scope(owner_id=owner_id, novel_id=novel_id, version_id=version_id)
        if not isinstance(package, CandidatePackage):
            raise TypeError("package must be a validated CandidatePackage")
        version = await self._version(
            owner_id=owner_id, novel_id=novel_id, version_id=version_id
        )
        if version is None:
            raise CandidateNotFoundError(
                "candidate version not found in explicit scope"
            )
        sealed = await self._session.scalar(
            select(NarrativeMemoryManifest.id).where(
                NarrativeMemoryManifest.owner_id == owner_id,
                NarrativeMemoryManifest.novel_id == novel_id,
                NarrativeMemoryManifest.version_id == version_id,
            )
        )
        if sealed is not None:
            raise CandidateConflictError("candidate version is already sealed")
        if any(
            link.hierarchy_build_id != version.hierarchy_build_id
            or link.source_snapshot_hash != version.source_snapshot_hash
            for link in package.source_links
        ):
            raise ScopeMismatchError("package source lineage does not match version")

        lineage = ModelLineage.model_validate(version.model_lineage)
        lineage_checksum = model_lineage_checksum(lineage)
        node_ids: dict[str, int] = {}
        for node in package.nodes:
            row = await self._insert_node(
                version=version,
                node=node,
                lineage_checksum=lineage_checksum,
            )
            node_ids[node.node_key] = row.id

        claim_ids: dict[str, int] = {}
        for claim in package.claims:
            row = await self._insert_claim(
                version=version,
                claim=claim,
                node_id=node_ids[claim.node_key],
                lineage_checksum=lineage_checksum,
            )
            claim_ids[claim.claim_key] = row.id

        edge_ids_list: list[int] = []
        for edge in package.edges:
            edge_row = await self._insert_edge(
                version=version,
                edge=edge,
                source_node_id=node_ids[edge.source_node_key],
                target_node_id=node_ids[edge.target_node_key],
                lineage_checksum=lineage_checksum,
            )
            edge_ids_list.append(edge_row.id)
        edge_ids = tuple(edge_ids_list)

        source_link_ids_list: list[int] = []
        for link in package.source_links:
            link_row = await self._insert_source_link(
                version=version,
                link=link,
                claim_id=claim_ids[link.claim_key],
                lineage_checksum=lineage_checksum,
            )
            source_link_ids_list.append(link_row.id)
        source_link_ids = tuple(source_link_ids_list)
        return PersistedCandidate(
            version_id=version.id,
            node_ids=node_ids,
            claim_ids=claim_ids,
            edge_ids=edge_ids,
            source_link_ids=source_link_ids,
        )

    async def _insert_node(
        self,
        *,
        version: NarrativeMemoryVersion,
        node: MemoryNode,
        lineage_checksum: str,
    ) -> NarrativeMemoryNode:
        checksum = node_checksum(node)
        expected = {
            "node_kind": node.node_kind.value,
            "chapter_start": node.chapter_start,
            "chapter_end": node.chapter_end,
            "schema_version": node.schema_version,
            "content_checksum": checksum,
            "model_lineage_checksum": lineage_checksum,
            "display_label": node.display_label,
        }
        existing = await self._session.scalar(
            select(NarrativeMemoryNode).where(
                NarrativeMemoryNode.owner_id == version.owner_id,
                NarrativeMemoryNode.novel_id == version.novel_id,
                NarrativeMemoryNode.version_id == version.id,
                NarrativeMemoryNode.node_key == node.node_key,
            )
        )
        if existing is not None:
            self._require_identical(existing, expected, label="node")
            return existing
        row = NarrativeMemoryNode(
            owner_id=version.owner_id,
            novel_id=version.novel_id,
            version_id=version.id,
            node_key=node.node_key,
            **expected,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _insert_claim(
        self,
        *,
        version: NarrativeMemoryVersion,
        claim: MemoryClaim,
        node_id: int,
        lineage_checksum: str,
    ) -> NarrativeMemoryClaim:
        checksum = claim_checksum(claim)
        expected = {
            "node_id": node_id,
            "claim_kind": claim.claim_kind,
            "schema_version": "memory-claim.v1",
            "typed_payload": claim.payload.model_dump(mode="json"),
            "uncertainty": claim.uncertainty.value,
            "confidence": claim.confidence,
            "visible_from_chapter": claim.visible_from_chapter,
            "claim_checksum": checksum,
            "model_lineage_checksum": lineage_checksum,
        }
        existing = await self._session.scalar(
            select(NarrativeMemoryClaim).where(
                NarrativeMemoryClaim.owner_id == version.owner_id,
                NarrativeMemoryClaim.novel_id == version.novel_id,
                NarrativeMemoryClaim.version_id == version.id,
                NarrativeMemoryClaim.claim_key == claim.claim_key,
            )
        )
        if existing is not None:
            self._require_identical(existing, expected, label="claim")
            return existing
        row = NarrativeMemoryClaim(
            owner_id=version.owner_id,
            novel_id=version.novel_id,
            version_id=version.id,
            claim_key=claim.claim_key,
            **expected,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _insert_edge(
        self,
        *,
        version: NarrativeMemoryVersion,
        edge: MemoryEdge,
        source_node_id: int,
        target_node_id: int,
        lineage_checksum: str,
    ) -> NarrativeMemoryEdge:
        checksum = edge_checksum(edge)
        expected = {
            "edge_checksum": checksum,
            "model_lineage_checksum": lineage_checksum,
        }
        existing = await self._session.scalar(
            select(NarrativeMemoryEdge).where(
                NarrativeMemoryEdge.owner_id == version.owner_id,
                NarrativeMemoryEdge.novel_id == version.novel_id,
                NarrativeMemoryEdge.version_id == version.id,
                NarrativeMemoryEdge.source_node_id == source_node_id,
                NarrativeMemoryEdge.target_node_id == target_node_id,
                NarrativeMemoryEdge.edge_type == edge.edge_type.value,
            )
        )
        if existing is not None:
            self._require_identical(existing, expected, label="edge")
            return existing
        row = NarrativeMemoryEdge(
            owner_id=version.owner_id,
            novel_id=version.novel_id,
            version_id=version.id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            edge_type=edge.edge_type.value,
            **expected,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _insert_source_link(
        self,
        *,
        version: NarrativeMemoryVersion,
        link: ExactSourceLink,
        claim_id: int,
        lineage_checksum: str,
    ) -> NarrativeMemorySourceLink:
        checksum = source_link_checksum(link)
        optional_ref = (
            {"source_key": link.optional_domain_source_key}
            if link.optional_domain_source_key
            else None
        )
        expected = {
            "source_kind": link.source_kind.value,
            "chapter_id": link.chapter_id,
            "chapter_number": link.chapter_number,
            "content_hash": link.content_hash,
            "source_snapshot_hash": link.source_snapshot_hash,
            "optional_source_ref": optional_ref,
            "link_checksum": checksum,
            "model_lineage_checksum": lineage_checksum,
        }
        existing = await self._session.scalar(
            select(NarrativeMemorySourceLink).where(
                NarrativeMemorySourceLink.owner_id == version.owner_id,
                NarrativeMemorySourceLink.novel_id == version.novel_id,
                NarrativeMemorySourceLink.version_id == version.id,
                NarrativeMemorySourceLink.claim_id == claim_id,
                NarrativeMemorySourceLink.hierarchy_build_id == link.hierarchy_build_id,
                NarrativeMemorySourceLink.evidence_node_id == link.evidence_node_id,
                NarrativeMemorySourceLink.source_start == link.source_start,
                NarrativeMemorySourceLink.source_end == link.source_end,
            )
        )
        if existing is not None:
            self._require_identical(existing, expected, label="source link")
            return existing
        row = NarrativeMemorySourceLink(
            owner_id=version.owner_id,
            novel_id=version.novel_id,
            version_id=version.id,
            claim_id=claim_id,
            hierarchy_build_id=link.hierarchy_build_id,
            evidence_node_id=link.evidence_node_id,
            source_start=link.source_start,
            source_end=link.source_end,
            **expected,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _version(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> NarrativeMemoryVersion | None:
        return await self._session.scalar(
            select(NarrativeMemoryVersion).where(
                NarrativeMemoryVersion.owner_id == owner_id,
                NarrativeMemoryVersion.novel_id == novel_id,
                NarrativeMemoryVersion.id == version_id,
            )
        )

    @staticmethod
    def _eligibility_checksum(report: EligibilityReport) -> str:
        encoded = f"narrative-memory.v1:eligibility-report\n{canonical_json(report)}"
        return sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _optional_lineage(report: EligibilityReport) -> list[dict[str, object]]:
        return [
            {
                "kind": asset.kind.value,
                "version_id": asset.version_id,
                "status": asset.status.value,
                "reason_codes": [reason.value for reason in asset.reason_codes],
                "item_count": asset.item_count,
                "healthy_empty": asset.healthy_empty,
            }
            for asset in report.assets
            if asset.kind != AssetKind.HIERARCHY
        ]

    @staticmethod
    def _require_identical(
        row: object, expected: dict[str, object], *, label: str
    ) -> None:
        mismatched = [
            field for field, value in expected.items() if getattr(row, field) != value
        ]
        if mismatched:
            raise CandidateConflictError(
                f"conflicting {label} retry: {', '.join(sorted(mismatched))}"
            )

    @staticmethod
    def _require_scope(
        *, owner_id: int, novel_id: int, version_id: int | None = None
    ) -> None:
        values = (
            (owner_id, novel_id)
            if version_id is None
            else (owner_id, novel_id, version_id)
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ScopeMismatchError(
                "scope identifiers must be explicit positive integers"
            )
