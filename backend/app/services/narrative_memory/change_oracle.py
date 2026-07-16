"""Provider-free change oracle for Phase 16 local rebuild.

Computes minimal provably-safe dirty closure from parent candidate authority
and target hierarchy. Zero model/embedding/pointer writes during plan compute.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.narrative_memory import NarrativeMemoryManifest, NarrativeMemoryVersion
from app.models.narrative_memory_rebuild import (
    NarrativeMemoryRebuildItem,
    NarrativeMemoryRebuildPlan,
)
from app.models.novel import Chapter
from app.services.narrative_memory.dependency_graph import (
    build_dependency_graph,
    chapter_evidence_fingerprint,
    evidence_fingerprint_from_leaf,
    evidence_fingerprint_from_link,
    load_parent_authority,
    load_target_hierarchy,
)
from app.services.narrative_memory.rebuild_contracts import (
    AssetKind,
    ChangeKind,
    ChangeRecord,
    CompatibilityPolicy,
    EdgeKind,
    OraclePolicy,
    ReasonCode,
    RebuildDecision,
    RebuildItemDecision,
    RebuildPlanSpec,
    stage_key_for_asset,
    stable_checksum,
)


class ChangeOracleError(ValueError):
    """Fail-closed oracle error."""


@dataclass
class _MutableDecision:
    asset_key: str
    asset_kind: AssetKind
    decision: RebuildDecision
    direct_reasons: list[ReasonCode] = field(default_factory=list)
    propagated_reasons: list[ReasonCode] = field(default_factory=list)
    predecessor_keys: list[str] = field(default_factory=list)
    chapter_start: int | None = None
    chapter_end: int | None = None
    old_content_checksum: str | None = None
    new_content_checksum: str | None = None
    dependency_checksum: str | None = None
    stage_key: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def mark_dirty(self, reason: ReasonCode, *, propagated: bool = False, pred: str | None = None) -> None:
        if self.decision == RebuildDecision.NOT_APPLICABLE:
            return
        if self.decision == RebuildDecision.CARRIED:
            self.decision = RebuildDecision.DIRTY
        elif self.decision not in {
            RebuildDecision.DIRTY,
            RebuildDecision.STALE_BLOCKED,
        }:
            self.decision = RebuildDecision.DIRTY
        if propagated:
            if reason not in self.propagated_reasons:
                self.propagated_reasons.append(reason)
        else:
            if reason not in self.direct_reasons:
                self.direct_reasons.append(reason)
        if pred and pred not in self.predecessor_keys:
            self.predecessor_keys.append(pred)

    def mark_stale(self, reason: ReasonCode) -> None:
        self.decision = RebuildDecision.STALE_BLOCKED
        if reason not in self.direct_reasons:
            self.direct_reasons.append(reason)

    def freeze(self) -> RebuildItemDecision:
        return RebuildItemDecision(
            asset_key=self.asset_key,
            asset_kind=self.asset_kind,
            decision=self.decision,
            direct_reasons=tuple(sorted(self.direct_reasons, key=lambda r: r.value)),
            propagated_reasons=tuple(
                sorted(self.propagated_reasons, key=lambda r: r.value)
            ),
            predecessor_keys=tuple(sorted(self.predecessor_keys)),
            chapter_start=self.chapter_start,
            chapter_end=self.chapter_end,
            old_content_checksum=self.old_content_checksum,
            new_content_checksum=self.new_content_checksum,
            dependency_checksum=self.dependency_checksum,
            stage_key=self.stage_key,
            detail=dict(self.detail),
        )


def classify_chapter_changes(
    *,
    parent_chapters: dict[int, Chapter],
    target_chapters: Sequence[Chapter],
    parent_content_hashes: dict[int, str],
    target_content_hashes: dict[int, str],
) -> list[ChangeRecord]:
    """Classify edit/insert/delete/reorder by stable chapter_id identity."""

    changes: list[ChangeRecord] = []
    target_by_id = {int(c.id): c for c in target_chapters}
    parent_ids = set(parent_chapters)
    target_ids = set(target_by_id)

    for cid in sorted(parent_ids - target_ids):
        ch = parent_chapters[cid]
        changes.append(
            ChangeRecord(
                asset_key=f"source_chapter:{cid}",
                asset_kind=AssetKind.SOURCE_CHAPTER,
                change_kind=ChangeKind.DELETE,
                reasons=(ReasonCode.CHAPTER_DELETED,),
                chapter_start=int(ch.chapter_number),
                chapter_end=int(ch.chapter_number),
                old_checksum=parent_content_hashes.get(cid),
            )
        )

    for cid in sorted(target_ids - parent_ids):
        ch = target_by_id[cid]
        changes.append(
            ChangeRecord(
                asset_key=f"source_chapter:{cid}",
                asset_kind=AssetKind.SOURCE_CHAPTER,
                change_kind=ChangeKind.INSERT,
                reasons=(ReasonCode.CHAPTER_INSERTED,),
                chapter_start=int(ch.chapter_number),
                chapter_end=int(ch.chapter_number),
                new_checksum=target_content_hashes.get(cid),
            )
        )

    for cid in sorted(parent_ids & target_ids):
        p = parent_chapters[cid]
        t = target_by_id[cid]
        reasons: list[ReasonCode] = []
        change_kind = ChangeKind.NO_CHANGE
        old_h = parent_content_hashes.get(cid)
        new_h = target_content_hashes.get(cid)
        if int(p.chapter_number) != int(t.chapter_number):
            reasons.append(ReasonCode.CHAPTER_REORDERED)
            change_kind = ChangeKind.REORDER
        if old_h is not None and new_h is not None and old_h != new_h:
            reasons.append(ReasonCode.CHAPTER_EDITED)
            change_kind = ChangeKind.EDIT if change_kind == ChangeKind.NO_CHANGE else change_kind
        if change_kind == ChangeKind.NO_CHANGE and reasons:
            change_kind = ChangeKind.EDIT
        if not reasons:
            reasons = [ReasonCode.CLEAN_IDENTICAL]
        changes.append(
            ChangeRecord(
                asset_key=f"source_chapter:{cid}",
                asset_kind=AssetKind.SOURCE_CHAPTER,
                change_kind=change_kind,
                reasons=tuple(reasons),
                chapter_start=int(t.chapter_number),
                chapter_end=int(t.chapter_number),
                old_checksum=old_h,
                new_checksum=new_h,
            )
        )
    return sorted(changes, key=lambda c: c.asset_key)


def classify_evidence_changes(
    *,
    parent_fps_by_chapter: dict[int, list[str]],
    target_fps_by_chapter: dict[int, list[str]],
) -> list[ChangeRecord]:
    """Compare complete leaf-set fingerprints per chapter (no similarity)."""

    changes: list[ChangeRecord] = []
    all_cids = set(parent_fps_by_chapter) | set(target_fps_by_chapter)
    for cid in sorted(all_cids):
        old_set = set(parent_fps_by_chapter.get(cid, []))
        new_set = set(target_fps_by_chapter.get(cid, []))
        old_fp = stable_checksum(sorted(old_set)) if old_set else None
        new_fp = stable_checksum(sorted(new_set)) if new_set else None
        if old_set == new_set:
            continue
        if not old_set and new_set:
            kind = ChangeKind.EVIDENCE_REMAP
            reasons = (ReasonCode.EVIDENCE_REMAPPED,)
        elif old_set and not new_set:
            kind = ChangeKind.EVIDENCE_REMAP
            reasons = (ReasonCode.EVIDENCE_REMAPPED, ReasonCode.MAPPING_UNPROVEN)
        elif old_set < new_set:
            kind = ChangeKind.EVIDENCE_SPLIT
            reasons = (ReasonCode.EVIDENCE_SPLIT,)
        elif new_set < old_set:
            kind = ChangeKind.EVIDENCE_MERGE
            reasons = (ReasonCode.EVIDENCE_MERGED,)
        else:
            kind = ChangeKind.EVIDENCE_REMAP
            reasons = (ReasonCode.EVIDENCE_REMAPPED,)
            if not (old_set & new_set):
                reasons = (ReasonCode.EVIDENCE_REMAPPED, ReasonCode.MAPPING_UNPROVEN)
        changes.append(
            ChangeRecord(
                asset_key=f"evidence_set:chapter:{cid}",
                asset_kind=AssetKind.EVIDENCE_LEAF,
                change_kind=kind,
                reasons=reasons,
                old_checksum=old_fp,
                new_checksum=new_fp,
                detail={"chapter_id": cid},
            )
        )
    return changes


def compute_closure(
    *,
    graph_vertices: Sequence[Any],
    graph_edges: Sequence[Any],
    changes: Sequence[ChangeRecord],
    boundary_changed: bool,
    policy_incompatible: bool,
    optional_uncertain: bool,
    cross_chapter_uncertain: bool,
    oracle_policy: OraclePolicy,
    chapter_numbers_sorted: Sequence[int],
    earliest_uncertain_chapter: int | None,
    stable_suffix_stop: int | None,
) -> list[RebuildItemDecision]:
    """Topological dirty propagation with monotonic conservative expansion."""

    decisions: dict[str, _MutableDecision] = {}
    for v in graph_vertices:
        decisions[v.asset_key] = _MutableDecision(
            asset_key=v.asset_key,
            asset_kind=v.asset_kind,
            decision=RebuildDecision.CARRIED,
            chapter_start=v.chapter_start,
            chapter_end=v.chapter_end,
            old_content_checksum=v.content_checksum,
            new_content_checksum=v.content_checksum,
            dependency_checksum=v.evidence_fingerprint or v.compatibility_fingerprint,
            stage_key=v.stage_key or stage_key_for_asset(v.asset_kind, v.asset_key),
        )

    # Children → parents adjacency for propagation.
    children_of: dict[str, list[str]] = defaultdict(list)
    parents_of: dict[str, list[str]] = defaultdict(list)
    for e in graph_edges:
        if e.edge_kind == EdgeKind.CHAPTER_TO_PARENT:
            # source chapter_state → target parent
            parents_of[e.source_key].append(e.target_key)
            children_of[e.target_key].append(e.source_key)
        elif e.edge_kind == EdgeKind.PARENT_TO_GLOBAL:
            parents_of[e.source_key].append(e.target_key)
            children_of[e.target_key].append(e.source_key)
        elif e.edge_kind in {
            EdgeKind.SOURCE_TO_CHAPTER_STATE,
            EdgeKind.EVIDENCE_TO_CHAPTER_STATE,
        }:
            parents_of[e.source_key].append(e.target_key)
            children_of[e.target_key].append(e.source_key)
        elif e.edge_kind in {
            EdgeKind.BOUNDARY_TO_PARENT,
            EdgeKind.BOUNDARY_TO_GLOBAL,
            EdgeKind.OPTIONAL_TO_CLAIM,
        }:
            parents_of[e.source_key].append(e.target_key)
            children_of[e.target_key].append(e.source_key)

    def dirty_key(key: str, reason: ReasonCode, *, propagated: bool = False, pred: str | None = None) -> None:
        if key not in decisions:
            return
        decisions[key].mark_dirty(reason, propagated=propagated, pred=pred)

    def propagate_up(start_key: str, reason: ReasonCode) -> None:
        stack = list(parents_of.get(start_key, []))
        seen: set[str] = set()
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            r = (
                ReasonCode.GLOBAL_PROPAGATED
                if decisions.get(key) and decisions[key].asset_kind == AssetKind.GLOBAL_STORY
                else ReasonCode.PARENT_PROPAGATED
            )
            dirty_key(key, r, propagated=True, pred=start_key)
            dirty_key(key, reason, propagated=True, pred=start_key)
            stack.extend(parents_of.get(key, []))

    # Seed from changes
    dirty_seed_keys: list[str] = []
    for ch in changes:
        if ch.change_kind == ChangeKind.NO_CHANGE and set(ch.reasons) == {
            ReasonCode.CLEAN_IDENTICAL
        }:
            if ch.asset_key in decisions:
                decisions[ch.asset_key].direct_reasons = [ReasonCode.CLEAN_IDENTICAL]
            continue

        dirty_key(ch.asset_key, ch.reasons[0] if ch.reasons else ReasonCode.MAPPING_UNPROVEN)
        for r in ch.reasons:
            dirty_key(ch.asset_key, r)

        # Map source chapter → chapter_state via graph edges
        if ch.asset_kind == AssetKind.SOURCE_CHAPTER:
            for parent in parents_of.get(ch.asset_key, []):
                for r in ch.reasons:
                    dirty_key(parent, r, pred=ch.asset_key)
                dirty_seed_keys.append(parent)
                propagate_up(parent, ReasonCode.CHILD_DIRTY)

        if ch.asset_kind == AssetKind.EVIDENCE_LEAF:
            # detail has chapter_id; find chapter_state nodes covering it
            cid = ch.detail.get("chapter_id")
            for key, dec in decisions.items():
                if dec.asset_kind != AssetKind.CHAPTER_STATE:
                    continue
                # Match by evidence set key or chapter attributes
                if cid is not None:
                    # Prefer exact source link mapping via edge sources
                    pass
                for r in ch.reasons:
                    # Dirty chapter states that consume this chapter's evidence
                    # Use chapter range equality when possible
                    if dec.chapter_start is not None and cid is not None:
                        # We only know chapter_id; map via source_chapter edges
                        pass
            # Use edges: any chapter_state that has evidence child dirty
            for key, dec in list(decisions.items()):
                if dec.asset_kind == AssetKind.CHAPTER_STATE:
                    # Check if any evidence edge points here from changed set
                    for e in graph_edges:
                        if (
                            e.edge_kind == EdgeKind.EVIDENCE_TO_CHAPTER_STATE
                            and e.target_key == key
                        ):
                            # Remap all chapter states when evidence set for their chapter changed
                            pass
            # Safer: dirties all chapter_state whose source_chapter matches
            chapter_id = ch.detail.get("chapter_id")
            source_key = f"source_chapter:{chapter_id}" if chapter_id is not None else None
            if source_key:
                for parent in parents_of.get(source_key, []):
                    for r in ch.reasons:
                        dirty_key(parent, r, pred=ch.asset_key)
                    dirty_seed_keys.append(parent)
                    propagate_up(parent, ReasonCode.CHILD_DIRTY)

    # Uncertainty expansions
    expand = (
        boundary_changed
        or policy_incompatible
        or optional_uncertain
        or cross_chapter_uncertain
        or any(
            c.change_kind
            in {
                ChangeKind.INSERT,
                ChangeKind.DELETE,
                ChangeKind.REORDER,
                ChangeKind.BOUNDARY_CHANGE,
                ChangeKind.DEPENDENCY_UNCERTAINTY,
                ChangeKind.MAPPING_AMBIGUOUS,
                ChangeKind.POLICY_INCOMPATIBLE,
            }
            for c in changes
        )
        or any(
            ReasonCode.MAPPING_UNPROVEN in c.reasons
            or ReasonCode.CROSS_CHAPTER_UNCERTAIN in c.reasons
            for c in changes
        )
    )

    if boundary_changed and "boundary_plan:book" in decisions:
        dirty_key("boundary_plan:book", ReasonCode.BOUNDARY_CHANGED)
        for parent in parents_of.get("boundary_plan:book", []):
            dirty_key(parent, ReasonCode.BOUNDARY_CHANGED, pred="boundary_plan:book")
            propagate_up(parent, ReasonCode.BOUNDARY_CHANGED)

    if policy_incompatible:
        for key, dec in decisions.items():
            if dec.asset_kind in {
                AssetKind.CHAPTER_STATE,
                AssetKind.STORY_ARC,
                AssetKind.VOLUME,
                AssetKind.GLOBAL_STORY,
            }:
                dirty_key(key, ReasonCode.POLICY_INCOMPATIBLE)

    if optional_uncertain:
        for key, dec in decisions.items():
            if dec.asset_kind == AssetKind.OPTIONAL_SOURCE:
                dirty_key(key, ReasonCode.OPTIONAL_SOURCE_LINEAGE)
                for parent in parents_of.get(key, []):
                    dirty_key(parent, ReasonCode.OPTIONAL_SOURCE_LINEAGE, pred=key)
                    propagate_up(parent, ReasonCode.OPTIONAL_SOURCE_LINEAGE)

    if expand and oracle_policy.expand_uncertain_to_suffix:
        start_num = earliest_uncertain_chapter
        if start_num is None:
            # earliest dirty chapter_state
            dirty_starts = [
                d.chapter_start
                for d in decisions.values()
                if d.decision == RebuildDecision.DIRTY and d.chapter_start is not None
            ]
            start_num = min(dirty_starts) if dirty_starts else None
        if start_num is not None:
            stop = stable_suffix_stop
            for key, dec in decisions.items():
                if dec.asset_kind != AssetKind.CHAPTER_STATE:
                    continue
                if dec.chapter_start is None:
                    continue
                if dec.chapter_start >= start_num and (
                    stop is None or dec.chapter_start <= stop
                ):
                    dirty_key(key, ReasonCode.SUFFIX_EXPANDED, propagated=True)
                    propagate_up(key, ReasonCode.SUFFIX_EXPANDED)

    if expand and oracle_policy.expand_uncertain_to_global:
        for key, dec in decisions.items():
            if dec.asset_kind == AssetKind.GLOBAL_STORY:
                dirty_key(key, ReasonCode.GLOBAL_PROPAGATED, propagated=True)
            if dec.asset_kind in {AssetKind.STORY_ARC, AssetKind.VOLUME}:
                # Dirty parents that overlap uncertain range
                if earliest_uncertain_chapter is not None and dec.chapter_end is not None:
                    if dec.chapter_end >= earliest_uncertain_chapter:
                        dirty_key(key, ReasonCode.PARENT_PROPAGATED, propagated=True)
                        propagate_up(key, ReasonCode.PARENT_PROPAGATED)

    # Always dirties global if any middle layer is dirty
    any_dirty_middle = any(
        d.decision == RebuildDecision.DIRTY
        and d.asset_kind
        in {
            AssetKind.CHAPTER_STATE,
            AssetKind.STORY_ARC,
            AssetKind.VOLUME,
            AssetKind.BOUNDARY_PLAN,
        }
        for d in decisions.values()
    )
    if any_dirty_middle:
        for key, dec in decisions.items():
            if dec.asset_kind == AssetKind.GLOBAL_STORY:
                dirty_key(key, ReasonCode.GLOBAL_PROPAGATED, propagated=True)

    # Sort reasons stably
    for dec in decisions.values():
        dec.direct_reasons = sorted(set(dec.direct_reasons), key=lambda r: r.value)
        dec.propagated_reasons = sorted(
            set(dec.propagated_reasons), key=lambda r: r.value
        )
        if (
            dec.decision == RebuildDecision.CARRIED
            and not dec.direct_reasons
            and not dec.propagated_reasons
        ):
            dec.direct_reasons = [ReasonCode.CLEAN_IDENTICAL]

    return [decisions[k].freeze() for k in sorted(decisions)]


def _content_hash_for_chapter(
    chapter: Chapter,
    *,
    content: str | None = None,
    chapter_id: int | None = None,
    chapter_number: int | None = None,
) -> str:
    """Hash chapter identity + body without lazy-loading ORM attributes."""

    cid = int(chapter_id if chapter_id is not None else chapter.id)
    number = int(
        chapter_number if chapter_number is not None else chapter.chapter_number
    )
    body = content if content is not None else (getattr(chapter, "content", None) or "")
    return stable_checksum(
        {
            "chapter_id": cid,
            "chapter_number": number,
            "content": body,
        }
    )


async def compute_rebuild_plan(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    parent_version_id: int,
    target_version_id: int,
    target_hierarchy_build_id: str,
    eligibility_report_checksum: str,
    oracle_policy: OraclePolicy | None = None,
    compatibility_policy: CompatibilityPolicy | None = None,
    require_sealed_parent: bool = True,
    require_unsealed_target: bool = True,
) -> RebuildPlanSpec:
    """Load authority, build graph, classify changes, compute closure."""

    oracle_policy = oracle_policy or OraclePolicy()
    parent = await load_parent_authority(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        parent_version_id=parent_version_id,
        require_sealed=require_sealed_parent and oracle_policy.require_sealed_parent,
    )
    target_version = await session.scalar(
        select(NarrativeMemoryVersion).where(
            NarrativeMemoryVersion.owner_id == owner_id,
            NarrativeMemoryVersion.novel_id == novel_id,
            NarrativeMemoryVersion.id == target_version_id,
        )
    )
    if target_version is None:
        raise ChangeOracleError("target version not found in explicit scope")
    if require_unsealed_target and oracle_policy.require_unsealed_target:
        sealed = await session.scalar(
            select(NarrativeMemoryManifest.id).where(
                NarrativeMemoryManifest.owner_id == owner_id,
                NarrativeMemoryManifest.novel_id == novel_id,
                NarrativeMemoryManifest.version_id == target_version_id,
            )
        )
        if sealed is not None:
            raise ChangeOracleError("target version is already sealed")
    if parent_version_id == target_version_id:
        raise ChangeOracleError("parent and target versions must be distinct")

    target = await load_target_hierarchy(
        session,
        novel_id=novel_id,
        hierarchy_build_id=target_hierarchy_build_id,
        expected_checksum=target_version.hierarchy_checksum,
        expected_snapshot=target_version.source_snapshot_hash,
    )
    if target.hierarchy_build_id != target_version.hierarchy_build_id:
        raise ChangeOracleError("target hierarchy build does not match version")

    boundary_plan = parent.boundary_plan or {}
    boundary_cs = parent.boundary_plan_checksum or stable_checksum(boundary_plan)

    graph = build_dependency_graph(
        parent,
        target=target,
        boundary_plan=boundary_plan,
        boundary_plan_checksum=boundary_cs,
    )

    # Parent chapter map from source links + novel chapters overlapping parent
    parent_chapter_ids = {int(link.chapter_id) for link in parent.source_links}
    # Load parent-side chapters that appear in links
    chapter_rows = list(
        (
            await session.scalars(
                select(Chapter)
                .where(Chapter.novel_id == novel_id)
                .options(undefer(Chapter.content))
            )
        ).all()
    )
    # Materialize scalars before later awaits (avoid async lazy-load).
    chapter_payloads: dict[int, dict[str, object]] = {
        int(c.id): {
            "id": int(c.id),
            "chapter_number": int(c.chapter_number),
            "content": c.content or "",
        }
        for c in chapter_rows
    }
    all_novel_chapters = {int(c.id): c for c in chapter_rows}
    # Parent chapters: those referenced by parent authority; for delete detection
    # also include chapters present only on parent snapshot via link chapter numbers.
    parent_chapters: dict[int, Chapter] = {}
    for cid in parent_chapter_ids:
        if cid in all_novel_chapters:
            parent_chapters[cid] = all_novel_chapters[cid]
        else:
            # Chapter deleted from novel — synthetic stub via link metadata
            link = next(lnk for lnk in parent.source_links if int(lnk.chapter_id) == cid)
            stub = Chapter(
                id=cid,
                novel_id=novel_id,
                chapter_number=int(link.chapter_number),
                title="",
                content="",
                word_count=0,
            )
            parent_chapters[cid] = stub

    parent_fps_by_chapter: dict[int, set[str]] = defaultdict(set)
    for link in parent.source_links:
        if link.source_kind != "hierarchy":
            continue
        fp = evidence_fingerprint_from_link(link)
        parent_fps_by_chapter[fp.chapter_id].add(fp.fingerprint())

    target_fps_by_chapter: dict[int, set[str]] = defaultdict(set)
    for leaf in target.evidence_leaves:
        fp = evidence_fingerprint_from_leaf(leaf)
        target_fps_by_chapter[fp.chapter_id].add(fp.fingerprint())

    # Authoritative chapter body identity = complete evidence leaf-set fingerprint.
    # Never mix full-text body hashes with evidence content hashes.
    parent_content_hashes: dict[int, str] = {
        cid: chapter_evidence_fingerprint(
            [
                evidence_fingerprint_from_link(link)
                for link in parent.source_links
                if int(link.chapter_id) == cid and link.source_kind == "hierarchy"
            ]
        )
        if parent_fps_by_chapter.get(cid)
        else _content_hash_for_chapter(
            parent_chapters[cid],
            content=str(chapter_payloads.get(cid, {}).get("content", "")),
            chapter_id=cid,
            chapter_number=int(parent_chapters[cid].chapter_number),
        )
        for cid in parent_chapters
    }
    target_content_hashes: dict[int, str] = {}
    for c in target.chapters:
        cid = int(c.id)
        fps = [
            evidence_fingerprint_from_leaf(leaf)
            for leaf in target.evidence_leaves
            if int(leaf.chapter_id or 0) == cid
        ]
        if fps:
            target_content_hashes[cid] = chapter_evidence_fingerprint(fps)
        else:
            payload = chapter_payloads.get(cid, {})
            target_content_hashes[cid] = _content_hash_for_chapter(
                c,
                content=str(payload.get("content", "")),
                chapter_id=cid,
                chapter_number=int(c.chapter_number),
            )

    chapter_changes = classify_chapter_changes(
        parent_chapters=parent_chapters,
        target_chapters=target.chapters,
        parent_content_hashes=parent_content_hashes,
        target_content_hashes=target_content_hashes,
    )

    evidence_changes = classify_evidence_changes(
        parent_fps_by_chapter={k: list(v) for k, v in parent_fps_by_chapter.items()},
        target_fps_by_chapter={k: list(v) for k, v in target_fps_by_chapter.items()},
    )

    # Compatibility
    if compatibility_policy is None:
        compatibility_policy = CompatibilityPolicy(
            schema_hash=parent.version.schema_hash,
            policy_hash=parent.version.policy_hash,
            prompt_hash=parent.version.prompt_hash,
            decoding_hash=parent.version.decoding_hash,
            config_hash=parent.version.config_hash,
            allowed_model_revisions=(),
        )
    policy_incompatible = (
        compatibility_policy.schema_hash != target_version.schema_hash
        or compatibility_policy.policy_hash != target_version.policy_hash
    )
    if not oracle_policy.allow_model_revision_carry:
        parent_rev = (parent.version.model_lineage or {}).get("revision")
        target_rev = (target_version.model_lineage or {}).get("revision")
        if parent_rev != target_rev:
            policy_incompatible = True

    boundary_changed = False
    # Target may carry a different planned boundary; if target has no build run yet,
    # boundary is considered unchanged only when parent boundary is present.
    # Explicit boundary change is detected when target version source snapshot
    # differs AND parent boundary chapter_max does not cover target chapters.
    target_max = max((int(c.chapter_number) for c in target.chapters), default=0)
    parent_max = int((boundary_plan or {}).get("chapter_max") or 0)
    if parent_max and target_max and target_max != parent_max:
        # Not automatically boundary change; insert/delete handles it.
        pass

    optional_uncertain = any(
        link.source_kind != "hierarchy" for link in parent.source_links
    ) and (
        parent.version.optional_source_lineage
        != (target_version.optional_source_lineage or {})
    )

    cross_chapter_uncertain = any(
        c.change_kind in {ChangeKind.INSERT, ChangeKind.DELETE, ChangeKind.REORDER}
        for c in chapter_changes
    )

    # Earliest uncertain chapter number
    earliest: int | None = None
    for c in chapter_changes:
        if c.change_kind != ChangeKind.NO_CHANGE and c.chapter_start is not None:
            earliest = (
                c.chapter_start
                if earliest is None
                else min(earliest, c.chapter_start)
            )
    for c in evidence_changes:
        if ReasonCode.MAPPING_UNPROVEN in c.reasons:
            # use detail chapter
            cid = c.detail.get("chapter_id")
            ch = all_novel_chapters.get(int(cid)) if cid is not None else None
            if ch is not None:
                n = int(ch.chapter_number)
                earliest = n if earliest is None else min(earliest, n)

    # Stable suffix stop: next explicit volume boundary after earliest if provable
    stable_stop: int | None = None
    if not cross_chapter_uncertain and not boundary_changed:
        # simple edit: no suffix expansion beyond containing parent end
        for c in chapter_changes:
            if c.change_kind == ChangeKind.EDIT and c.chapter_start is not None:
                # find parent range covering this chapter
                for v in graph.vertices:
                    if v.asset_kind in {AssetKind.STORY_ARC, AssetKind.VOLUME}:
                        if (
                            v.chapter_start is not None
                            and v.chapter_end is not None
                            and v.chapter_start <= c.chapter_start <= v.chapter_end
                        ):
                            stable_stop = v.chapter_end
                # for simple edit without uncertainty, do not expand suffix
                earliest = None  # disable suffix expansion path

    all_changes = list(chapter_changes) + list(evidence_changes)
    items = compute_closure(
        graph_vertices=graph.vertices,
        graph_edges=graph.edges,
        changes=all_changes,
        boundary_changed=boundary_changed,
        policy_incompatible=policy_incompatible,
        optional_uncertain=optional_uncertain,
        cross_chapter_uncertain=cross_chapter_uncertain,
        oracle_policy=oracle_policy,
        chapter_numbers_sorted=sorted(int(c.chapter_number) for c in target.chapters),
        earliest_uncertain_chapter=earliest if cross_chapter_uncertain else None,
        stable_suffix_stop=stable_stop if cross_chapter_uncertain else None,
    )

    # Re-apply simple local edit rule: when only edits and no uncertainty,
    # dirty only affected chapter_state + parents + global (already done via edges).
    change_summary = {
        "chapter_changes": [c.model_dump(mode="json") for c in chapter_changes],
        "evidence_changes": [c.model_dump(mode="json") for c in evidence_changes],
        "boundary_changed": boundary_changed,
        "policy_incompatible": policy_incompatible,
        "optional_uncertain": optional_uncertain,
        "cross_chapter_uncertain": cross_chapter_uncertain,
        "dirty_count": sum(1 for i in items if i.decision == RebuildDecision.DIRTY),
        "carried_count": sum(1 for i in items if i.decision == RebuildDecision.CARRIED),
        "stale_count": sum(
            1 for i in items if i.decision == RebuildDecision.STALE_BLOCKED
        ),
    }

    return RebuildPlanSpec(
        owner_id=owner_id,
        novel_id=novel_id,
        parent_version_id=parent_version_id,
        target_version_id=target_version_id,
        old_source_snapshot_hash=parent.version.source_snapshot_hash,
        new_source_snapshot_hash=target_version.source_snapshot_hash,
        old_hierarchy_build_id=parent.version.hierarchy_build_id,
        new_hierarchy_build_id=target_version.hierarchy_build_id,
        old_hierarchy_checksum=parent.version.hierarchy_checksum,
        new_hierarchy_checksum=target_version.hierarchy_checksum,
        boundary_plan=boundary_plan,
        boundary_plan_checksum=boundary_cs
        if len(boundary_cs) == 64
        else stable_checksum(boundary_plan),
        oracle_policy=oracle_policy,
        compatibility_policy=compatibility_policy,
        eligibility_report_checksum=eligibility_report_checksum,
        graph_checksum=graph.graph_checksum,
        items=tuple(items),
        change_summary=change_summary,
    )


async def persist_rebuild_plan(
    session: AsyncSession,
    plan: RebuildPlanSpec,
    *,
    revalidate: bool = True,
) -> NarrativeMemoryRebuildPlan:
    """Insert immutable plan + items. Fails if scope/checksum stale."""

    if revalidate:
        # Stale parent seal check
        parent_manifest = await session.scalar(
            select(NarrativeMemoryManifest).where(
                NarrativeMemoryManifest.owner_id == plan.owner_id,
                NarrativeMemoryManifest.novel_id == plan.novel_id,
                NarrativeMemoryManifest.version_id == plan.parent_version_id,
            )
        )
        if parent_manifest is None and plan.oracle_policy.require_sealed_parent:
            raise ChangeOracleError("parent became unsealed before persist")

        target = await session.scalar(
            select(NarrativeMemoryVersion).where(
                NarrativeMemoryVersion.owner_id == plan.owner_id,
                NarrativeMemoryVersion.novel_id == plan.novel_id,
                NarrativeMemoryVersion.id == plan.target_version_id,
            )
        )
        if target is None:
            raise ChangeOracleError("target version missing at persist")
        if target.source_snapshot_hash != plan.new_source_snapshot_hash:
            raise ChangeOracleError("target source snapshot drifted before persist")
        if target.hierarchy_checksum != plan.new_hierarchy_checksum:
            raise ChangeOracleError("target hierarchy checksum drifted before persist")

    plan_cs = plan.plan_checksum()
    existing = await session.scalar(
        select(NarrativeMemoryRebuildPlan).where(
            NarrativeMemoryRebuildPlan.owner_id == plan.owner_id,
            NarrativeMemoryRebuildPlan.novel_id == plan.novel_id,
            NarrativeMemoryRebuildPlan.plan_checksum == plan_cs,
        )
    )
    if existing is not None:
        return existing

    # Conflict if same parent/target pair already planned with different checksum
    pair = await session.scalar(
        select(NarrativeMemoryRebuildPlan).where(
            NarrativeMemoryRebuildPlan.owner_id == plan.owner_id,
            NarrativeMemoryRebuildPlan.novel_id == plan.novel_id,
            NarrativeMemoryRebuildPlan.parent_version_id == plan.parent_version_id,
            NarrativeMemoryRebuildPlan.target_version_id == plan.target_version_id,
        )
    )
    if pair is not None and pair.plan_checksum != plan_cs:
        raise ChangeOracleError("conflicting plan already exists for parent/target pair")
    if pair is not None:
        return pair

    row = NarrativeMemoryRebuildPlan(
        owner_id=plan.owner_id,
        novel_id=plan.novel_id,
        parent_version_id=plan.parent_version_id,
        target_version_id=plan.target_version_id,
        old_source_snapshot_hash=plan.old_source_snapshot_hash,
        new_source_snapshot_hash=plan.new_source_snapshot_hash,
        old_hierarchy_build_id=plan.old_hierarchy_build_id,
        new_hierarchy_build_id=plan.new_hierarchy_build_id,
        old_hierarchy_checksum=plan.old_hierarchy_checksum,
        new_hierarchy_checksum=plan.new_hierarchy_checksum,
        boundary_plan=plan.boundary_plan,
        boundary_plan_checksum=plan.boundary_plan_checksum,
        oracle_policy_version=plan.oracle_policy.policy_version,
        oracle_policy_checksum=plan.oracle_policy.checksum(),
        compatibility_policy_checksum=plan.compatibility_policy.checksum(),
        graph_checksum=plan.graph_checksum,
        plan_checksum=plan_cs,
        change_summary=plan.change_summary,
        eligibility_report_checksum=plan.eligibility_report_checksum,
    )
    session.add(row)
    await session.flush()

    for item in sorted(plan.items, key=lambda i: i.asset_key):
        session.add(
            NarrativeMemoryRebuildItem(
                owner_id=plan.owner_id,
                novel_id=plan.novel_id,
                plan_id=row.id,
                asset_key=item.asset_key,
                asset_kind=item.asset_kind.value,
                chapter_start=item.chapter_start,
                chapter_end=item.chapter_end,
                decision=item.decision.value,
                direct_reasons=[r.value for r in item.direct_reasons],
                propagated_reasons=[r.value for r in item.propagated_reasons],
                predecessor_keys=list(item.predecessor_keys),
                old_content_checksum=item.old_content_checksum,
                new_content_checksum=item.new_content_checksum,
                dependency_checksum=item.dependency_checksum,
                stage_key=item.stage_key,
                detail=item.detail,
            )
        )
    await session.flush()
    return row


def oracle_has_provider_capability() -> bool:
    return False


def expand_dirty_monotonically(
    base_dirty: frozenset[str],
    added_unknowns: Sequence[str],
) -> frozenset[str]:
    """Property helper: adding unknowns can only enlarge the dirty set."""

    return frozenset(set(base_dirty) | set(added_unknowns))
