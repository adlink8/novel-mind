"""Strict, side-effect-free contracts for narrative-memory asset eligibility."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AssetKind(StrEnum):
    HIERARCHY = "hierarchy"
    TIMELINE = "timeline"
    RELATIONSHIP = "relationship"
    CLUE = "clue"


class AssetRequirement(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"


class EligibilityStatus(StrEnum):
    REUSABLE_EXACT = "reusable_exact"
    REBUILD_REQUIRED = "rebuild_required"
    BLOCKED = "blocked"
    OPTIONAL_UNAVAILABLE = "optional_unavailable"


class ReasonCode(StrEnum):
    SOURCE_MISSING = "source_missing"
    SOURCE_UNAVAILABLE = "source_unavailable"
    OWNER_SCOPE_MISMATCH = "owner_scope_mismatch"
    NOVEL_SCOPE_MISMATCH = "novel_scope_mismatch"
    ACTIVE_VERSION_MISSING = "active_version_missing"
    SOURCE_SNAPSHOT_MISMATCH = "source_snapshot_mismatch"
    MANIFEST_MISMATCH = "manifest_mismatch"
    MALFORMED_HIERARCHY = "malformed_hierarchy"
    INVALID_OFFSET = "invalid_offset"
    CONTENT_HASH_MISMATCH = "content_hash_mismatch"
    INCOMPLETE_COVERAGE = "incomplete_coverage"
    OPTIONAL_LINEAGE_MISMATCH = "optional_lineage_mismatch"
    STALE_ASSET = "stale_asset"


class RebuildRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Imported books may use chapter 0 for a preface/prologue.
    start_chapter: int = Field(ge=0)
    end_chapter: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> "RebuildRange":
        if self.end_chapter < self.start_chapter:
            raise ValueError("end_chapter must be >= start_chapter")
        return self


class AssetInventory(BaseModel):
    """Lossless observation returned by a read-only source adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AssetKind
    owner_id: int = Field(ge=1)
    novel_id: int = Field(ge=1)
    version_id: str | None = Field(default=None, min_length=1, max_length=160)
    source_snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    manifest_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    item_count: int = Field(default=0, ge=0)
    available: bool = True
    healthy_empty: bool = False
    reason_codes: tuple[ReasonCode, ...] = ()
    rebuild_ranges: tuple[RebuildRange, ...] = ()

    @field_validator("reason_codes")
    @classmethod
    def canonical_reasons(cls, value: tuple[ReasonCode, ...]) -> tuple[ReasonCode, ...]:
        if len(set(value)) != len(value):
            raise ValueError("reason_codes must be unique")
        return tuple(sorted(value, key=str))

    @field_validator("rebuild_ranges")
    @classmethod
    def canonical_ranges(
        cls, value: tuple[RebuildRange, ...]
    ) -> tuple[RebuildRange, ...]:
        ordered = tuple(
            sorted(value, key=lambda item: (item.start_chapter, item.end_chapter))
        )
        if len({(item.start_chapter, item.end_chapter) for item in ordered}) != len(
            ordered
        ):
            raise ValueError("rebuild_ranges must be unique")
        return ordered

    @model_validator(mode="after")
    def validate_availability(self) -> "AssetInventory":
        if self.healthy_empty and (not self.available or self.item_count != 0):
            raise ValueError(
                "healthy_empty requires an available source with item_count=0"
            )
        if not self.available and self.healthy_empty:
            raise ValueError("an unavailable source cannot be healthy_empty")
        return self


class AssetEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AssetKind
    requirement: AssetRequirement
    owner_id: int = Field(ge=1)
    novel_id: int = Field(ge=1)
    version_id: str | None = None
    status: EligibilityStatus
    reason_codes: tuple[ReasonCode, ...] = ()
    rebuild_ranges: tuple[RebuildRange, ...] = ()
    item_count: int = Field(default=0, ge=0)
    healthy_empty: bool = False

    @field_validator("reason_codes")
    @classmethod
    def canonical_reasons(cls, value: tuple[ReasonCode, ...]) -> tuple[ReasonCode, ...]:
        if len(set(value)) != len(value):
            raise ValueError("reason_codes must be unique")
        return tuple(sorted(value, key=str))

    @field_validator("rebuild_ranges")
    @classmethod
    def canonical_ranges(
        cls, value: tuple[RebuildRange, ...]
    ) -> tuple[RebuildRange, ...]:
        ordered = tuple(
            sorted(value, key=lambda item: (item.start_chapter, item.end_chapter))
        )
        if len({(item.start_chapter, item.end_chapter) for item in ordered}) != len(
            ordered
        ):
            raise ValueError("rebuild_ranges must be unique")
        return ordered


class EligibilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "asset-eligibility.v1"
    policy_version: str = "asset-eligibility-policy.v1"
    owner_id: int = Field(ge=1)
    novel_id: int = Field(ge=1)
    assets: tuple[AssetEligibility, ...]
    provider_calls_allowed: bool

    @model_validator(mode="before")
    @classmethod
    def derive_provider_guard(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        raw_assets = value.get("assets", ())
        assets = tuple(
            item
            if isinstance(item, AssetEligibility)
            else AssetEligibility.model_validate(item)
            for item in raw_assets
        )
        required = [
            item for item in assets if item.requirement == AssetRequirement.REQUIRED
        ]
        expected = bool(required) and all(
            item.status == EligibilityStatus.REUSABLE_EXACT for item in required
        )
        supplied = value.get("provider_calls_allowed")
        if supplied is not None and supplied != expected:
            raise ValueError(
                "provider_calls_allowed must be derived from required assets"
            )
        return {**value, "provider_calls_allowed": expected}

    @field_validator("assets")
    @classmethod
    def canonical_assets(
        cls, value: tuple[AssetEligibility, ...]
    ) -> tuple[AssetEligibility, ...]:
        if len({item.kind for item in value}) != len(value):
            raise ValueError("asset kinds must be unique")
        return tuple(sorted(value, key=lambda item: item.kind.value))

    @model_validator(mode="after")
    def validate_complete_scope(self) -> "EligibilityReport":
        if {item.kind for item in self.assets} != set(AssetKind):
            raise ValueError(
                "report must contain exactly one result for every asset kind"
            )
        if any(
            item.owner_id != self.owner_id or item.novel_id != self.novel_id
            for item in self.assets
        ):
            raise ValueError("asset scope must match report scope")
        expected_requirements = {
            AssetKind.HIERARCHY: AssetRequirement.REQUIRED,
            AssetKind.TIMELINE: AssetRequirement.OPTIONAL,
            AssetKind.RELATIONSHIP: AssetRequirement.OPTIONAL,
            AssetKind.CLUE: AssetRequirement.OPTIONAL,
        }
        if any(
            item.requirement != expected_requirements[item.kind] for item in self.assets
        ):
            raise ValueError(
                "asset requirements are policy-defined and cannot be caller supplied"
            )
        return self
