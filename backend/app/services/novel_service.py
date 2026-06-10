"""
小说处理核心服务

本模块是小说导入流程的核心，负责:
1. 文件上传与编码检测（支持 UTF-8、GBK、GB2312 等中文编码）
2. 文本清洗（去除 BOM、统一换行、合并空行）
3. 章节自动分割（5 种正则模式匹配中英文章节标题）
4. 数据库 CRUD（创建、查询、删除小说和章节）

章节分割正则支持:
  - 中文: "第一章"、"第1回"、"第一百二十章"、"第壹章"
  - 英文: "Chapter 1"、"CHAPTER 1"
  - 数字: "1. 标题"、"1、标题"

编码检测回退链:
  chardet 检测 → GBK → GB2312 → UTF-8-SIG → UTF-8
"""

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
# 5 种模式按优先级排列，覆盖中英文常见章节标题格式
CHAPTER_PATTERNS = [
    r"^第[零一二三四五六七八九十百千\d]+[章节回卷集篇部幕].*$",   # 中文章节号（"第一章 xxx"）
    r"^[第]?[零一二三四五六七八九十百千\d]+[章节回卷集篇部幕].*$", # 宽松中文章节（可省略"第"）
    r"^Chapter\s+\d+.*$",                                        # 英文 Chapter（"Chapter 1 xxx"）
    r"^CHAPTER\s+\d+.*$",                                        # 大写 CHAPTER
    r"^\d+[\.\s、].+$",                                           # 数字标题（"1. xxx" 或 "1、xxx"）
]

# 合并为单一正则（OR 关系，多行模式下逐行匹配）
CHAPTER_REGEX = re.compile(
    "|".join(f"(?:{p})" for p in CHAPTER_PATTERNS),
    re.MULTILINE,
)


class NovelService:
    """小说处理核心服务（全局单例模式）"""

    # ─────────── 文件上传与编码检测 ───────────

    async def upload_novel(self, file: UploadFile) -> Tuple[str, str]:
        """
        保存上传文件并读取内容。

        Args:
            file: FastAPI UploadFile 对象

        Returns:
            (文件保存路径, 解码后的文本内容)

        Raises:
            ValueError: 文件过大或编码无法识别
        """
        # 确保上传目录存在
        os.makedirs(settings.upload_dir, exist_ok=True)

        # 读取原始字节
        raw = await file.read()
        if len(raw) > settings.max_upload_size:
            raise ValueError(f"文件大小超过限制 ({settings.max_upload_size // 1024 // 1024}MB)")

        # 自动检测编码（chardet 基于统计学方法）
        detected = chardet.detect(raw)
        encoding = detected.get("encoding") or "utf-8"
        logger.info(f"检测到文件编码: {encoding} (置信度 {detected.get('confidence', 0):.2f})")

        # 尝试解码，失败则按回退链逐个尝试
        try:
            content = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            for fallback in ("gbk", "gb2312", "utf-8-sig", "utf-8"):
                try:
                    content = raw.decode(fallback)
                    logger.info(f"使用回退编码: {fallback}")
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError("无法识别文件编码，请使用 UTF-8 或 GBK 编码")

        # 保存为 UTF-8 编码的文本文件
        safe_name = file.filename or "unknown.txt"
        save_path = os.path.join(settings.upload_dir, safe_name)
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(content)

        return save_path, content

    # ─────────── 文本解析与章节分割 ───────────

    def parse_novel(self, content: str) -> List[dict]:
        """
        清洗文本并分割为章节列表。

        Args:
            content: 小说全文文本

        Returns:
            章节列表: [{"chapter_number": 1, "title": "...", "content": "...", "word_count": N}, ...]

        处理流程:
        1. 去除 BOM 标记（\ufeff）
        2. 统一换行符（\r\n / \r → \n）
        3. 合并连续空行（3+ 空行 → 2 空行）
        4. 用正则匹配章节标题位置
        5. 按标题位置切分文本
        6. 计算每章字数
        """
        # 基础清洗
        content = content.lstrip("\ufeff")                    # 去除 UTF-8 BOM
        content = content.replace("\r\n", "\n").replace("\r", "\n")  # 统一换行符
        content = re.sub(r"\n{3,}", "\n\n", content)       # 多个空行合并为两个

        # 查找所有章节标题位置
        matches = list(CHAPTER_REGEX.finditer(content))

        chapters: List[dict] = []

        if not matches:
            # 未检测到章节标记 → 全文作为单章
            logger.info("未检测到章节标记，全文作为单章处理")
            chapters.append({
                "chapter_number": 1,
                "title": "全文",
                "content": content.strip(),
            })
        else:
            # 处理第一个章节标题前的前言部分（如有）
            preamble = content[: matches[0].start()].strip()
            if preamble and len(preamble) > 100:  # 前言超过 100 字才单独成章
                chapters.append({
                    "chapter_number": 0,
                    "title": "前言",
                    "content": preamble,
                })

            # 按标题位置切分各章节
            for idx, match in enumerate(matches):
                start = match.start()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)

                title_line = match.group().strip()
                body = content[start:end].strip()

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
        """
        创建小说及章节数据库记录。

        Args:
            db: 数据库会话
            title: 小说标题
            chapters: parse_novel 返回的章节列表
            source_path: 原始文件路径
            author: 作者（可选）

        Returns:
            创建的 Novel ORM 对象
        """
        total_words = sum(ch["word_count"] for ch in chapters)

        # 创建小说记录
        novel = Novel(
            title=title,
            author=author,
            chapter_count=len(chapters),
            word_count=total_words,
            status="importing",
            source_path=source_path,
        )
        db.add(novel)
        await db.flush()  # 获取 novel.id（flush 到数据库但不 commit）

        # 批量创建章节记录
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

        # 更新状态为 ready（导入完成）
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

        Args:
            db: 数据库会话
            skip: 跳过记录数（分页偏移）
            limit: 返回记录数（每页大小）
            search: 搜索关键词（匹配标题或作者）

        Returns:
            (小说列表, 总数)
        """
        query = select(Novel).order_by(Novel.created_at.desc())
        count_query = select(func.count(Novel.id))

        # 搜索过滤（标题或作者模糊匹配）
        if search:
            like_pattern = f"%{search}%"
            query = query.where(
                Novel.title.ilike(like_pattern) | Novel.author.ilike(like_pattern)
            )
            count_query = count_query.where(
                Novel.title.ilike(like_pattern) | Novel.author.ilike(like_pattern)
            )

        # 获取总数
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        # 分页查询
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        novels = list(result.scalars().all())

        return novels, total

    async def get_novel(self, db: AsyncSession, novel_id: int) -> Optional[Novel]:
        """获取单本小说（通过 selectin 预加载 chapters 关系）"""
        result = await db.execute(select(Novel).where(Novel.id == novel_id))
        return result.scalar_one_or_none()

    async def delete_novel(self, db: AsyncSession, novel_id: int) -> bool:
        """
        删除小说及其所有关联数据。

        行为:
        1. 删除源文件（uploads 目录下的 TXT）
        2. ORM 级联删除 chapters、text_chunks 等关联数据
        3. flush 到数据库

        Returns:
            True 删除成功，False 小说不存在
        """
        novel = await self.get_novel(db, novel_id)
        if not novel:
            return False

        # 删除源文件
        if novel.source_path and os.path.exists(novel.source_path):
            os.remove(novel.source_path)
            logger.info(f"已删除源文件: {novel.source_path}")

        # ORM 级联删除（cascade="all, delete-orphan"）
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
