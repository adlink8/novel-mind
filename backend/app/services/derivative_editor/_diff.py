"""Deterministic canonical-Markdown diff primitives (leaf).

Extracted from ``revisions.py`` (refactor split): ``_split_lines`` and
``diff_markdown`` are pure line-diff helpers shared by the diff/history query
paths. Leaf by construction — imports only stdlib + the chapter canonicalization
helpers, never ``revisions.py``. The revision facade re-exports these names so
the ``app.services.derivative_editor.revisions`` import surface is unchanged.
"""

from __future__ import annotations

import difflib
from typing import Any

from app.services.derivative_editor.chapters import canonicalize_markdown


def _split_lines(text: str) -> list[str]:
    """Canonical Markdown -> line list; the empty draft is an empty list."""
    if text == "":
        return []
    return text.split("\n")


def diff_markdown(old_text: str, new_text: str) -> list[dict[str, Any]]:
    """Deterministic line diff between two canonical Markdown documents.

    Both sides are canonicalized first (CRLF → LF, trailing whitespace
    stripped, D-36-02) so identical logical content always diffs to zero hunks.
    Returns unified-diff-style hunks: each hunk carries 1-based line numbers
    (``old_start``/``old_count``/``new_start``/``new_count``) and ordered lines
    with an op of ``delete``, ``add`` or ``context`` (3 lines of context around
    each contiguous change).
    """
    old_lines = _split_lines(canonicalize_markdown(old_text))
    new_lines = _split_lines(canonicalize_markdown(new_text))
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    hunks: list[dict[str, Any]] = []
    for group in matcher.get_grouped_opcodes(n=3):
        first = group[0]
        last = group[-1]
        old_start = first[1] + 1
        old_count = last[2] - first[1]
        new_start = first[3] + 1
        new_count = last[4] - first[3]
        lines: list[dict[str, str]] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for line in old_lines[i1:i2]:
                    lines.append({"op": "context", "text": line})
            elif tag == "replace":
                for line in old_lines[i1:i2]:
                    lines.append({"op": "delete", "text": line})
                for line in new_lines[j1:j2]:
                    lines.append({"op": "add", "text": line})
            elif tag == "delete":
                for line in old_lines[i1:i2]:
                    lines.append({"op": "delete", "text": line})
            elif tag == "insert":
                for line in new_lines[j1:j2]:
                    lines.append({"op": "add", "text": line})
        hunks.append(
            {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "lines": lines,
            }
        )
    return hunks


__all__ = ["_split_lines", "diff_markdown"]
