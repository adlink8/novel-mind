"""Static forbidden-capability scanner for the narrative-memory builder package.

Extracted from ``builder_worker.py``: this leaf module owns the deny-list
constant and the ``scan_builder_package_for_forbidden_capabilities`` AST/import
scanner used by the forbidden-capability contract tests (reader-chat imports,
promotion/pointer fragments must never appear in builder_*.py). It is a leaf —
it imports only from the standard library, so the scanner can be re-exported
from ``builder_worker`` without creating import cycles.
"""

from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_IMPORT_FRAGMENTS = (
    "reader_chat",
    "ReaderConversation",
    "ReaderMessage",
    "promote_timeline",
    "promote_clue",
    "TimelineActivePointer",
    "ClueActivePointer",
    "NarrativeActivePointer",
    "current_version",
    "set_active_pointer",
)


def scan_builder_package_for_forbidden_capabilities(
    package_dir: Path | None = None,
) -> list[str]:
    """Static AST/import scan used by forbidden-capability tests."""

    root = package_dir or Path(__file__).resolve().parent
    hits: list[str] = []
    for path in sorted(root.glob("builder_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for frag in FORBIDDEN_IMPORT_FRAGMENTS:
                        if frag in alias.name:
                            hits.append(f"{path.name}:import:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for frag in FORBIDDEN_IMPORT_FRAGMENTS:
                    if frag in module:
                        hits.append(f"{path.name}:from:{module}")
                    for alias in node.names:
                        if frag in alias.name:
                            hits.append(f"{path.name}:name:{alias.name}")
    # Also scan sibling modules introduced by later plans.
    for name in (
        "arc_planner.py",
        "global_builder.py",
        "optional_sources.py",
        "builder_report.py",
    ):
        path = root / name
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        for frag in ("reader_chat", "ReaderConversation", "set_active_pointer"):
            if frag in source:
                hits.append(f"{name}:text:{frag}")
    return hits
