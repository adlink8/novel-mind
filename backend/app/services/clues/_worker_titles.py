"""Clue worker title helpers — short hypothesis title resolution.

拆分说明（refactor split）：judge 判断后生成产品标题的纯函数下沉到本叶模块——
``build_machine_clue_title`` 与 ``resolve_machine_clue_title`` 被
``_persist_decision`` 和标题诚实的单测直接引用。纯字符串逻辑，无 DB/网络依赖。
"""

from __future__ import annotations

__all__ = [
    "MAX_SHORT_TITLE_LEN",
    "TITLE_SOURCE_JUDGE_SHORT_TITLE",
    "TITLE_SOURCE_RATIONALE_OR_STEM",
    "build_machine_clue_title",
    "resolve_machine_clue_title",
]

MAX_SHORT_TITLE_LEN = 40

TITLE_SOURCE_JUDGE_SHORT_TITLE = "judge_short_title"
TITLE_SOURCE_RATIONALE_OR_STEM = "rationale_or_chapter_stem"


def _clean_title_stem(text: str, *, max_len: int = 24) -> str:
    """Collapse whitespace and take a short stem for product titles."""
    cleaned = " ".join((text or "").replace("\r", "\n").split())
    if not cleaned:
        return ""
    # Prefer first sentence-like fragment.
    for sep in ("。", "！", "？", ".", "!", "?", "；", ";"):
        if sep in cleaned:
            cleaned = cleaned.split(sep, 1)[0].strip()
            break
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def _clean_short_title(text: str | None, *, max_len: int = MAX_SHORT_TITLE_LEN) -> str:
    """Collapse whitespace in a judge-provided display title and clip length."""
    cleaned = " ".join((text or "").replace("\r", "\n").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def resolve_machine_clue_title(
    *,
    short_title: str | None,
    rationale: str | None,
    cue_text: str | None,
    chapter: int | None,
    candidate_id: str,
    max_len: int = 32,
) -> tuple[str, str]:
    """Return ``(title, title_source)`` with judge short_title as first choice.

    Falls back to the historical rationale/chapter-stem heuristic when the
    judge did not provide a usable short title. ``title_source`` is recorded
    honestly in the package snapshot.
    """
    cleaned = _clean_short_title(short_title)
    if len(cleaned) >= 2:
        return cleaned, TITLE_SOURCE_JUDGE_SHORT_TITLE
    return (
        build_machine_clue_title(
            rationale=rationale,
            cue_text=cue_text,
            chapter=chapter,
            candidate_id=candidate_id,
            max_len=max_len,
        ),
        TITLE_SOURCE_RATIONALE_OR_STEM,
    )


def build_machine_clue_title(
    *,
    rationale: str | None,
    cue_text: str | None,
    chapter: int | None,
    candidate_id: str,
    max_len: int = 32,
) -> str:
    """Short hypothesis title — never the raw long cue excerpt alone.

    Prefer the first cleaned line of the judgment rationale; otherwise
    ``伏笔·第N章`` + a short stem from cue text.
    """
    rationale_line = ""
    if rationale:
        first = (rationale.replace("\r", "\n").split("\n", 1)[0] or "").strip()
        rationale_line = _clean_title_stem(first, max_len=max_len)
    if rationale_line and len(rationale_line) >= 2:
        return rationale_line[:max_len]

    stem = _clean_title_stem(cue_text or "", max_len=16)
    if chapter is not None and int(chapter) > 0:
        prefix = f"伏笔·第{int(chapter)}章"
        if stem:
            title = f"{prefix}·{stem}"
        else:
            title = prefix
        return title[:max_len]

    if stem:
        return f"伏笔·{stem}"[:max_len]
    return (candidate_id or "伏笔候选")[:max_len]
