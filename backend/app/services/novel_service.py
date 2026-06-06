"""小说处理服务 - 上传、解析、CRUD"""

import os
import re
import logging
from typing import List, Optional, Tuple

import chardet
from fastapi import UploadFile
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.novel import Novel, Chapter

logger = logging.getLogger(__name__)

# ────────────────────── 章节分割正则模式 ──────────────────────
# 支持: 第一章 / 第1章 / 第一回 / Chapter 1 / 第壹章 等
CHAPTER_PATTERNS = [
    r"^第[零一二三四五六七八九十百千\d]+[章节回卷集篇部幕].*$",   # 中文章节号
    r"^[第]?[零一二三四五六七八九十百千\d]+[章节回卷集篇部幕].*$", # 宽松匹配
    r"^Chapter\s+\d+.*$",                                        # 英文 Chapter
    r"^CHAPTER\s+\d+.*$",                                        # 大写 CHAPTER
    r"^\d+[\.\s、].+$",                                           # "1. 标题" 或 "1、标题"
]

# 合并为单一正则（多行模式下逐行匹配）
CHAPTER_REGEX = re.compile(
    "|".join(f"(?:{p})" for p in CHAPTER_PATTERNS),
    re.MULTILINE,
)


class NovelService:
    """小说处理核心服务"""

    # ─────────── 文件上传与编码检测 ───────────

    async def upload_novel(self, file: UploadFile) -> Tuple[str, str]:
        """
        保存上传文件并读取内容。
        返回 (文件保存路径, 解码后的文本内容)。
        """
        # 确保上传目录存在
        os.makedirs(settings.upload_dir, exist_ok=True)

        raw = await file.read()
        if len(raw) > settings.max_upload_size:
            raise ValueError(f"文件大小超过限制 ({settings.max_upload_size // 1024 // 1024}MB)")

        # 自动检测编码
        detected = chardet.detect(raw)
        encoding = detected.get("encoding") or "utf-8"
        logger.info(f"检测到文件编码: {encoding} (置信度 {detected.get('confidence', 0):.2f})")

        try:
            content = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            # 回退到 GBK → UTF-8
            for fallback in ("gbk", "gb2312", "utf-8-sig", "utf-8"):
                try:
                    content = raw.decode(fallback)
                    logger.info(f"使用回退编码: {fallback}")
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError("无法识别文件编码，请使用 UTF-8 或 GBK 编码")

        # 保存原始文件
        safe_name = file.filename or "unknown.txt"
        save_path = os.path.join(settings.upload_dir, safe_name)
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(content)

        return save_path, content

    # ─────────── 文本解析与章节分割 ───────────

    def parse_novel(self, content: str) -> List[dict]:
        """
        清洗文本并分割为章节列表。
        返回 [{"chapter_number": 1, "title": "...", "content": "..."}, ...]
        若未检测到章节标记，则将全文作为单章处理。
        """
        # 基础清洗：去除 BOM、统一换行符、去除多余空白
        content = content.lstrip("\ufeff")
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        content = re.sub(r"\n{3,}", "\n\n", content)  # 多个空行合并为两个

        # 查找所有章节标题位置
        matches = list(CHAPTER_REGEX.finditer(content))

        chapters: List[dict] = []

        if not matches:
            # 未检测到章节标记 → 全文作为第一章
            logger.info("未检测到章节标记，全文作为单章处理")
            chapters.append({
                "chapter_number": 1,
                "title": "全文",
                "content": content.strip(),
            })
        else:
            # 处理第一章标题前的前言部分（如果有）
            preamble = content[: matches[0].start()].strip()
            if preamble and len(preamble) > 100:
                chapters.append({
                    "chapter_number": 0,
                    "title": "前言",
                    "content": preamble,
                })

            for idx, match in enumerate(matches):
                start = match.start()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)

                title_line = match.group().strip()
                body = content[start:end].strip()

                # 如果正文以标题行开头，保留标题在正文中
                chapters.append({
                    "chapter_number": idx + 1,
                    "title": title_line[:100],  # 截断过长标题
                    "content": body,
                })

        # 计算每章字数
        for ch in chapters:
            ch["word_count"] = len(ch["content"])

        logger.info(f"章节分割完成: 共 {len(chapters)} 章")
        return chapters

    # ─────────── 数据库 CRUD ───────────

    async def create_novel_record(
        self,
        db: AsyncSession,
        title: str,
        chapters: List[dict],
        source_path: Optional[str] = None,
        author: Optional[str] = None,
    ) -> Novel:
        """创建小说及章节数据库记录"""
        total_words = sum(ch["word_count"] for ch in chapters)

        novel = Novel(
            title=title,
            author=author,
            chapter_count=len(chapters),
            word_count=total_words,
            status="importing",
            source_path=source_path,
        )
        db.add(novel)
        await db.flush()  # 获取 novel.id

        # 批量创建章节
        for ch in chapters:
            chapter = Chapter(
                novel_id=novel.id,
                chapter_number=ch["chapter_number"],
                title=ch["title"],
                content=ch["content"],
                word_count=ch["word_count"],
            )
            db.add(chapter)

        await db.flush()

        # 更新状态为 ready
        novel.status = "ready"
        await db.flush()

        logger.info(f"小说已入库: {title} (ID={novel.id}, {len(chapters)} 章, {total_words} 字)")
        return novel

    async def get_novels(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> Tuple[List[Novel], int]:
        """
        查询小说列表（带分页与搜索）。
        返回 (小说列表, 总数)。
        """
        query = select(Novel).order_by(Novel.created_at.desc())
        count_query = select(func.count(Novel.id))

        if search:
            like_pattern = f"%{search}%"
            query = query.where(
                Novel.title.ilike(like_pattern) | Novel.author.ilike(like_pattern)
            )
            count_query = count_query.where(
                Novel.title.ilike(like_pattern) | Novel.author.ilike(like_pattern)
            )

        # 总数
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        # 分页
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        novels = list(result.scalars().all())

        return novels, total

    async def get_novel(self, db: AsyncSession, novel_id: int) -> Optional[Novel]:
        """获取单本小说（含章节列表，通过 selectin 预加载）"""
        result = await db.execute(select(Novel).where(Novel.id == novel_id))
        return result.scalar_one_or_none()

    async def delete_novel(self, db: AsyncSession, novel_id: int) -> bool:
        """删除小说及其所有关联数据（级联删除）"""
        novel = await self.get_novel(db, novel_id)
        if not novel:
            return False

        # 删除源文件
        if novel.source_path and os.path.exists(novel.source_path):
            os.remove(novel.source_path)
            logger.info(f"已删除源文件: {novel.source_path}")

        await db.delete(novel)
        await db.flush()
        logger.info(f"已删除小说: {novel.title} (ID={novel_id})")
        return True

    async def get_chapter(self, db: AsyncSession, chapter_id: int) -> Optional[Chapter]:
        """获取单个章节"""
        result = await db.execute(select(Chapter).where(Chapter.id == chapter_id))
        return result.scalar_one_or_none()


# 全局单例
novel_service = NovelService()
