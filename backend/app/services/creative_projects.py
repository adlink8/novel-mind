"""Owner-scoped creative project editing without generation or model calls."""

import difflib
import hashlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fanfiction import FanFiction
from app.models.fanfiction_chapter import FanFictionChapter
from app.models.fanfiction_revision import FanFictionRevision
from app.models.fanfiction_override import FanFictionOverride
from app.models.novel import Chapter, Novel
from app.schemas.fanfiction import (
    FanFictionChapterCreate,
    FanFictionChapterUpdate,
    FanFictionCreate,
    FanFictionUpdate,
    FanFictionOverrideCreate,
)


class CreativeProjectNotFound(LookupError):
    """Project or child resource is outside the caller's owner scope."""


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def owned_novel(
    db: AsyncSession, *, novel_id: int, owner_id: int, is_superuser: bool
) -> Novel:
    novel = await db.get(Novel, novel_id)
    if novel is None or (not is_superuser and novel.owner_id != owner_id):
        raise CreativeProjectNotFound("novel not found")
    return novel


async def get_project(
    db: AsyncSession, *, project_id: int, owner_id: int, is_superuser: bool
) -> FanFiction:
    query = (
        select(FanFiction)
        .join(Novel, Novel.id == FanFiction.novel_id)
        .where(FanFiction.id == project_id)
    )
    if not is_superuser:
        query = query.where(Novel.owner_id == owner_id)
    project = (await db.execute(query)).scalar_one_or_none()
    if project is None:
        raise CreativeProjectNotFound("creative project not found")
    return project


async def list_projects(db: AsyncSession, *, novel: Novel) -> list[FanFiction]:
    return list(
        (
            await db.scalars(
                select(FanFiction)
                .where(FanFiction.novel_id == novel.id)
                .order_by(FanFiction.updated_at.desc(), FanFiction.id.desc())
            )
        ).all()
    )


async def create_project(db: AsyncSession, *, data: FanFictionCreate) -> FanFiction:
    project = FanFiction(
        novel_id=data.novel_id,
        title=data.title,
        prompt=data.prompt,
        content="",
        status="draft",
        parent_chapter_id=data.parent_chapter_id,
    )
    if data.parent_chapter_id is not None:
        chapter = await db.scalar(
            select(Chapter).where(
                Chapter.id == data.parent_chapter_id,
                Chapter.novel_id == data.novel_id,
            )
        )
        if chapter is None:
            raise ValueError("parent_chapter_id 不属于该小说")
    db.add(project)
    await db.flush()
    await _write_revision(
        db, project=project, chapter=None, title=project.title, content=""
    )
    await db.commit()
    await db.refresh(project)
    return project


async def update_project(
    db: AsyncSession, *, project: FanFiction, data: FanFictionUpdate
) -> FanFiction:
    values = data.model_dump(exclude_unset=True)
    if not values:
        return project
    for key, value in values.items():
        setattr(project, key, value)
    if "content" in values or "title" in values:
        await _write_revision(
            db,
            project=project,
            chapter=None,
            title=project.title,
            content=project.content or "",
        )
    await db.commit()
    await db.refresh(project)
    return project


async def list_chapters(
    db: AsyncSession, *, project: FanFiction
) -> list[FanFictionChapter]:
    return list(
        (
            await db.scalars(
                select(FanFictionChapter)
                .where(FanFictionChapter.fanfiction_id == project.id)
                .order_by(FanFictionChapter.chapter_number, FanFictionChapter.id)
            )
        ).all()
    )


async def create_chapter(
    db: AsyncSession, *, project: FanFiction, data: FanFictionChapterCreate
) -> FanFictionChapter:
    chapter = FanFictionChapter(
        fanfiction_id=project.id,
        chapter_number=data.chapter_number,
        title=data.title,
        content=data.content,
        word_count=len(data.content),
        ai_generated=False,
    )
    db.add(chapter)
    await db.flush()
    await _write_revision(
        db,
        project=project,
        chapter=chapter,
        title=chapter.title,
        content=chapter.content or "",
    )
    await db.commit()
    await db.refresh(chapter)
    return chapter


async def update_chapter(
    db: AsyncSession,
    *,
    chapter: FanFictionChapter,
    data: FanFictionChapterUpdate,
    project: FanFiction,
) -> FanFictionChapter:
    values = data.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(chapter, key, value)
    if "content" in values:
        chapter.word_count = len(chapter.content or "")
    if values:
        await _write_revision(
            db,
            project=project,
            chapter=chapter,
            title=chapter.title,
            content=chapter.content or "",
        )
    await db.commit()
    await db.refresh(chapter)
    return chapter


async def get_chapter(
    db: AsyncSession, *, project: FanFiction, chapter_id: int
) -> FanFictionChapter:
    chapter = await db.scalar(
        select(FanFictionChapter).where(
            FanFictionChapter.id == chapter_id,
            FanFictionChapter.fanfiction_id == project.id,
        )
    )
    if chapter is None:
        raise CreativeProjectNotFound("chapter not found")
    return chapter


async def list_revisions(
    db: AsyncSession, *, project: FanFiction
) -> list[FanFictionRevision]:
    return list(
        (
            await db.scalars(
                select(FanFictionRevision)
                .where(FanFictionRevision.fanfiction_id == project.id)
                .order_by(FanFictionRevision.revision_number.desc())
            )
        ).all()
    )


async def list_overrides(
    db: AsyncSession, *, project: FanFiction
) -> list[FanFictionOverride]:
    return list(
        (
            await db.scalars(
                select(FanFictionOverride)
                .where(FanFictionOverride.fanfiction_id == project.id)
                .order_by(
                    FanFictionOverride.created_at.desc(), FanFictionOverride.id.desc()
                )
            )
        ).all()
    )


async def create_override(
    db: AsyncSession, *, project: FanFiction, data: FanFictionOverrideCreate
) -> FanFictionOverride:
    existing = await db.scalar(
        select(FanFictionOverride).where(
            FanFictionOverride.fanfiction_id == project.id,
            FanFictionOverride.override_key == data.override_key,
        )
    )
    if existing is not None:
        raise ValueError("override_key 已存在于该创作项目")
    override = FanFictionOverride(
        fanfiction_id=project.id,
        override_key=data.override_key,
        statement=data.statement,
        reason=data.reason,
        original_evidence_key=data.original_evidence_key,
        status="active",
    )
    db.add(override)
    await db.commit()
    await db.refresh(override)
    return override


async def get_revision(
    db: AsyncSession, *, project: FanFiction, revision_id: int
) -> FanFictionRevision:
    revision = await db.scalar(
        select(FanFictionRevision).where(
            FanFictionRevision.id == revision_id,
            FanFictionRevision.fanfiction_id == project.id,
        )
    )
    if revision is None:
        raise CreativeProjectNotFound("revision not found")
    return revision


def revision_diff(
    *, from_revision: FanFictionRevision, to_revision: FanFictionRevision
) -> str:
    return "".join(
        difflib.unified_diff(
            from_revision.content.splitlines(keepends=True),
            to_revision.content.splitlines(keepends=True),
            fromfile=f"revision-{from_revision.revision_number}",
            tofile=f"revision-{to_revision.revision_number}",
        )
    )


async def rollback_revision(
    db: AsyncSession, *, project: FanFiction, revision: FanFictionRevision
) -> tuple[FanFictionRevision, int | None, bool]:
    if revision.chapter_id is None:
        project.title = revision.title
        project.content = revision.content
        project.word_count = len(revision.content)
        restored_chapter_id = None
        restored_project = True
        chapter = None
    else:
        chapter = await get_chapter(db, project=project, chapter_id=revision.chapter_id)
        chapter.title = revision.title
        chapter.content = revision.content
        chapter.word_count = len(revision.content)
        restored_chapter_id = chapter.id
        restored_project = False

    restored = await _write_revision(
        db,
        project=project,
        chapter=chapter,
        title=revision.title,
        content=revision.content,
        editor_kind="rollback",
    )
    await db.commit()
    await db.refresh(restored)
    return restored, restored_chapter_id, restored_project


async def _write_revision(
    db: AsyncSession,
    *,
    project: FanFiction,
    chapter: FanFictionChapter | None,
    title: str,
    content: str,
    editor_kind: str = "user",
) -> FanFictionRevision:
    latest = await db.scalar(
        select(func.max(FanFictionRevision.revision_number)).where(
            FanFictionRevision.fanfiction_id == project.id
        )
    )
    revision = FanFictionRevision(
        fanfiction_id=project.id,
        chapter_id=chapter.id if chapter else None,
        revision_number=int(latest or 0) + 1,
        title=title,
        content=content,
        content_hash=_hash(content),
        editor_kind=editor_kind,
    )
    db.add(revision)
    return revision
