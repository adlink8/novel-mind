"""Single capability authority for the 23 built-in agent tools.

The catalog is deliberately data-only. It does not add arbitrary HTTP, Python or
shell execution and is safe to project to an owner-authenticated settings UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.agent_tools.facade import TOOL_NAMES

ToolCategory = Literal["read", "candidate", "action"]

_READ_TOOLS = frozenset(
    {
        "get_novel",
        "get_chapter",
        "search_novel_text",
        "get_timeline",
        "get_relationships",
        "get_clues",
        "get_narrative_memory",
        "get_events",
        "get_character_state",
        "get_character_knowledge",
        "get_world_rules",
        "get_evidence_span",
        "get_visual_bible",
    }
)
_CANDIDATE_TOOLS = frozenset(
    {
        "generate_image_candidate",
        "publish_illustration",
        "attach_illustration_to_text",
        "create_canon_fork",
        "apply_derivative_edit",
        "allow_divergence",
        "publish_derivative_revision",
        "publish_derivative_visual",
        "approve_export",
    }
)


@dataclass(frozen=True)
class ToolCapability:
    name: str
    category: ToolCategory
    approval_required: bool
    user_configurable: bool = True


def _category(name: str) -> ToolCategory:
    if name in _READ_TOOLS:
        return "read"
    if name in _CANDIDATE_TOOLS:
        return "candidate"
    if name == "materialize_export":
        return "action"
    raise RuntimeError(f"tool {name!r} is missing from the capability catalog")


def list_tool_capabilities() -> tuple[ToolCapability, ...]:
    """Return the deterministic catalog in the facade's public order."""
    capabilities = []
    for name in TOOL_NAMES:
        category = _category(name)
        capabilities.append(
            ToolCapability(
                name=name,
                category=category,
                approval_required=(
                    category != "read" and name != "generate_image_candidate"
                ),
            )
        )
    return tuple(capabilities)


TOOL_CAPABILITIES = list_tool_capabilities()
TOOL_CAPABILITY_NAMES = frozenset(item.name for item in TOOL_CAPABILITIES)
