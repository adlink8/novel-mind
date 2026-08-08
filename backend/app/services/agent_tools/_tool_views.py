"""Leaf module of JSON-safe tool response views shared by agent-tool domains.

Extracted from the agent-tools facade (Phase 34-39 action tools): each view
turns a candidate proposal / fork / job ORM envelope into the JSON-safe payload
the tool contract returns. Every view is candidate-only — status stays
pending_approval / proposed / queued, never published — and carries the
approval_request_id / payload_hash the Web approval flow and the deterministic
publisher/materializer reference. This module is a leaf: it imports nothing
from sibling agent-tool modules, so visual and derivative domains can both
consume it without creating import cycles.
"""

from __future__ import annotations

from typing import Any


def _agent_edit_proposal_view_for_tool(result) -> dict[str, Any]:
    """DerivativeEditProposal approval ORM → JSON-safe 工具响应。

    candidate-only：proposal_status 恒为 proposed、绝不 applied；approval_request_id /
    payload_hash 供 Web 审批轮询与确定性 Revision Service 引用。
    """
    return {
        "proposal_key": result.proposal_key,
        "owner_id": result.owner_id,
        "novel_id": result.novel_id,
        "project_id": result.project_id,
        "chapter_id": result.chapter_id,
        "base_revision": result.base_revision,
        "content_hash": result.content_hash,
        "approval_request_id": result.approval_request_id,
        "approval_action": result.approval_action,
        "approval_status": result.approval_status,
        "approval_payload_hash": result.approval_payload_hash,
        "status": "candidate",
        "candidate_only": True,
        "replayed": bool(result.replayed),
    }


def _fork_proposal_view_for_tool(result) -> dict[str, Any]:
    """CanonFork + ApprovalRequest ORM → JSON-safe 工具响应。

    candidate-only：fork status 恒为 candidate、active 恒 false，绝不 approved/
    published；approval_request_id / payload_hash 供 Web 审批轮询与确定性 Fork
    materializer 引用。
    """
    fork = result.fork
    approval = result.approval_request
    return {
        "fork_id": fork.id,
        "owner_id": fork.owner_id,
        "novel_id": fork.novel_id,
        "fork_key": fork.fork_key,
        "space": fork.space,
        "status": fork.status,
        "source_version_key": fork.source_version_key,
        "source_snapshot_id": fork.source_snapshot_id,
        "source_snapshot_hash": fork.source_snapshot_hash,
        "through_chapter": fork.through_chapter,
        "full_book_authorized": fork.full_book_authorized,
        "cutoff_snapshot_hash": fork.cutoff_snapshot_hash,
        "scope_hash": fork.scope_hash,
        "manifest_hash": fork.manifest_hash,
        "delta_content_hash": result.delta_content_hash,
        "approval_request_id": approval.id,
        "approval_action": approval.action,
        "approval_status": approval.status,
        "approval_payload_hash": approval.payload_hash,
        "active": bool(fork.active),
        "candidate_only": True,
        "replayed": bool(result.replayed),
    }


def _anchor_proposal_view_for_tool(result) -> dict[str, Any]:
    """IllustrationAnchorProposal + ApprovalRequest ORM → JSON-safe 工具响应。

    candidate-only：status 恒为 pending_approval/proposed，绝不 published；
    approval_request_id / payload_hash 供 Web 审批轮询与确定性 publisher 引用。
    """
    proposal = result.proposal
    approval = result.approval_request
    return {
        "proposal_id": proposal.id,
        "owner_id": proposal.owner_id,
        "novel_id": proposal.novel_id,
        "chapter_id": proposal.chapter_id,
        "chapter_number": proposal.chapter_number,
        "proposal_key": proposal.proposal_key,
        "source_start": proposal.source_start,
        "source_end": proposal.source_end,
        "anchor_hash": proposal.anchor_hash,
        "proposal_asset_revision_id": proposal.proposal_asset_revision_id,
        "approval_request_id": approval.id,
        "approval_action": approval.action,
        "approval_status": approval.status,
        "approval_payload_hash": approval.payload_hash,
        "status": proposal.status,
        "candidate_only": True,
        "replayed": bool(result.replayed),
    }


def _job_view_for_tool(job) -> dict[str, Any]:
    """IllustrationJob ORM → JSON-safe 工具响应（候选作业读信封，永不 published）。"""
    return {
        "id": job.id,
        "owner_id": job.owner_id,
        "novel_id": job.novel_id,
        "job_key": job.job_key,
        "idempotency_key": job.idempotency_key,
        "status": job.status,
        "status_reason": job.status_reason,
        "error_code": job.error_code,
        "retry_count": job.retry_count,
        "scene_spec_hash": job.scene_spec_hash,
        "prompt_revision_id": job.prompt_revision_id,
        "prompt_revision_hash": job.prompt_revision_hash,
        "visual_bible_revision_hash": job.visual_bible_revision_hash,
        "source_snapshot_id": job.source_snapshot_id,
        "source_snapshot_hash": job.source_snapshot_hash,
        "cutoff_chapter": job.cutoff_chapter,
        "config_hash": job.config_hash,
        "candidate_only": True,
    }
