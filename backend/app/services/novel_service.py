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
import secrets
from typing import List, Optional, Tuple, Dict

import chardet
from fastapi import UploadFile
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.novel import Novel, Chapter

logger = logging.getLogger(__name__)

# ────────────────────── 导入进度跟踪 ──────────────────────
# 内存中的导入状态跟踪（用于前端轮询进度）
# 格式: {novel_id: {"stage": "uploading", "percent": 10, "message": "..."}}
_import_status: Dict[int, dict] = {}

# 扩展编码回退链（覆盖中文繁体、日文、西欧编码）
_ENCODING_FALLBACKS = (
    "gb18030",  # 国标扩展（兼容 GBK、GB2312，覆盖全部中文生僻字）
    "gbk",  # 国标扩展（Windows 中文默认）
    "gb2312",  # 简体中文
    "big5",  # 繁体中文（中国台湾、香港常用）
    "shift_jis",  # 日文
    "euc-jp",  # 日文 EUC
    "utf-8-sig",  # 带 BOM 的 UTF-8
    "utf-8",  # 标准 UTF-8
    "iso-8859-1",  # 西欧单字节编码（兜底）
)


def _detect_encoding(raw: bytes) -> Tuple[str, float]:
    """
    检测字节流编码。

    先用 chardet 统计检测，若置信度低则按回退链逐个尝试。

    Returns:
        (编码名称, 置信度 0-1)
    """
    detected = chardet.detect(raw)
    encoding = detected.get("encoding") or "utf-8"
    confidence = detected.get("confidence", 0)

    # chardet 对中文检测常返回 GB2312，但 GB18030 更兼容
    if encoding and encoding.lower() in ("gb2312", "gbk"):
        encoding = "gb18030"
        confidence = max(confidence, 0.7)

    return encoding, confidence


def _decode_with_fallback(raw: bytes) -> str:
    """
    使用检测到的编码或回退链解码字节流。

    Args:
        raw: 原始字节

    Returns:
        解码后的字符串

    Raises:
        ValueError: 所有编码均无法解码
    """
    encoding, confidence = _detect_encoding(raw)
    logger.info(f"检测到文件编码: {encoding} (置信度 {confidence:.2f})")

    # 先尝试检测到的编码
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        pass

    # 按回退链逐个尝试
    for fallback in _ENCODING_FALLBACKS:
        try:
            text = raw.decode(fallback)
            logger.info(f"使用回退编码: {fallback}")
            return text
        except UnicodeDecodeError:
            continue

    raise ValueError("无法识别文件编码，请使用 UTF-8 或 GBK 编码")


# ────────────────────── 章节分割正则模式 ──────────────────────
# 5 种模式按优先级排列，覆盖中英文常见章节标题格式
CHAPTER_PATTERNS = [
    r"^第[零一二三四五六七八九十百千\d]+[章节回卷集篇部幕].*$",  # 中文章节号（"第一章 xxx"）
    r"^[第]?[零一二三四五六七八九十百千\d]+[章节回卷集篇部幕].*$",  # 宽松中文章节（可省略"第"）
    r"^Chapter\s+\d+.*$",  # 英文 Chapter（"Chapter 1 xxx"）
    r"^CHAPTER\s+\d+.*$",  # 大写 CHAPTER
    r"^\d+[\.\s、].+$",  # 数字标题（"1. xxx" 或 "1、xxx"）
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
        保存上传文件并读取内容（支持大文件流式读取和多编码自动检测）。

        安全处理:
        - 使用随机文件名防止路径穿越和文件名冲突
        - 限制文件必须保存在 upload_dir 目录内（路径 containment）
        - 边读取边检查大小，避免大文件耗尽内存
        - 使用临时文件 + 原子重命名，防止数据库失败时遗留不完整文件
        - 原始文件名仅用于提取标题，不用于存储

        Args:
            file: FastAPI UploadFile 对象

        Returns:
            (文件保存路径, 解码后的文本内容)

        Raises:
            ValueError: 文件过大或编码无法识别
        """
        # 确保上传目录存在（绝对路径，防止相对路径穿越）
        upload_dir = os.path.abspath(os.path.normpath(settings.upload_dir))
        os.makedirs(upload_dir, exist_ok=True)

        # 生成安全的随机文件名（16 字符十六进制 + .txt 后缀）
        random_name = secrets.token_hex(8) + ".txt"
        save_path = os.path.join(upload_dir, random_name)
        temp_path = save_path + ".tmp"

        # 二次确认保存路径在 upload_dir 内（防止路径穿越）
        real_save_path = os.path.abspath(os.path.normpath(save_path))
        real_temp_path = os.path.abspath(os.path.normpath(temp_path))
        if (
            not real_save_path.startswith(upload_dir + os.sep)
            and real_save_path != upload_dir
        ):
            raise ValueError("非法的文件保存路径")

        # 边读取边检查大小（避免一次性读入大文件耗尽内存）
        chunks = []
        total_size = 0
        while True:
            chunk = await file.read(64 * 1024)  # 64KB 分块读取
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > settings.max_upload_size:
                raise ValueError(
                    f"文件大小超过限制 ({settings.max_upload_size // 1024 // 1024}MB)"
                )
            chunks.append(chunk)

        raw = b"".join(chunks)

        # 使用多编码回退链解码
        content = _decode_with_fallback(raw)

        # 先写入临时文件，再原子重命名（防止数据库失败时遗留不完整文件）
        with open(real_temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(real_temp_path, real_save_path)

        logger.info(
            f"文件已保存: {random_name} (原文件名: {file.filename}, {total_size / 1024:.1f} KB, 编码已统一为 UTF-8)"
        )
        return real_save_path, content

    def remove_uploaded_file(self, source_path: str) -> None:
        """Remove a source file only when it is contained by upload_dir."""
        upload_dir = os.path.realpath(os.path.abspath(settings.upload_dir))
        real_source_path = os.path.realpath(os.path.abspath(source_path))
        if os.path.commonpath([upload_dir, real_source_path]) != upload_dir:
            logger.error("拒绝删除上传目录外文件: %s", real_source_path)
            return
        if os.path.exists(real_source_path):
            os.remove(real_source_path)

    # ─────────── 导入进度跟踪 ───────────

    def set_import_status(
        self, novel_id: int, stage: str, percent: int, message: str = ""
    ) -> None:
        """设置小说导入状态（供前端轮询）"""
        _import_status[novel_id] = {
            "stage": stage,
            "percent": min(max(percent, 0), 100),
            "message": message or stage,
        }

    def get_import_status(self, novel_id: int) -> Optional[dict]:
        """获取小说导入状态"""
        return _import_status.get(novel_id)

    def clear_import_status(self, novel_id: int) -> None:
        """清理导入状态（导入完成后）"""
        _import_status.pop(novel_id, None)

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
        content = content.lstrip("\ufeff")  # 去除 UTF-8 BOM
        content = content.replace("\r\n", "\n").replace("\r", "\n")  # 统一换行符
        content = re.sub(r"\n{3,}", "\n\n", content)  # 多个空行合并为两个

        # 查找所有章节标题位置
        matches = list(CHAPTER_REGEX.finditer(content))

        chapters: List[dict] = []

        if not matches:
            # 未检测到章节标记 → 全文作为单章
            logger.info("未检测到章节标记，全文作为单章处理")
            chapters.append(
                {
                    "chapter_number": 1,
                    "title": "全文",
                    "content": content.strip(),
                }
            )
        else:
            # 处理第一个章节标题前的前言部分（如有）
            preamble = content[: matches[0].start()].strip()
            if preamble and len(preamble) > 100:  # 前言超过 100 字才单独成章
                chapters.append(
                    {
                        "chapter_number": 0,
                        "title": "前言",
                        "content": preamble,
                    }
                )

            # 按标题位置切分各章节
            for idx, match in enumerate(matches):
                start = match.start()
                end = (
                    matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
                )

                title_line = match.group().strip()
                body = content[start:end].strip()

                chapters.append(
                    {
                        "chapter_number": idx + 1,
                        "title": title_line[:100],  # 截断过长标题
                        "content": body,
                    }
                )

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
        owner_id: Optional[int] = None,
    ) -> Novel:
        """
        创建小说及章节数据库记录（带导入进度跟踪）。

        Args:
            db: 数据库会话
            title: 小说标题
            chapters: parse_novel 返回的章节列表
            source_path: 原始文件路径
            author: 作者（可选）
            owner_id: 上传者用户ID（可选）

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
            owner_id=owner_id,
        )
        db.add(novel)
        await db.flush()  # 获取 novel.id

        # 记录导入进度：开始保存章节
        self.set_import_status(novel.id, "saving", 60, "正在保存章节到数据库...")

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

        # 记录导入完成
        self.set_import_status(
            novel.id, "ready", 100, f"导入完成：{len(chapters)} 章，{total_words} 字"
        )
        logger.info(
            f"小说已入库: {title} (ID={novel.id}, {len(chapters)} 章, {total_words} 字)"
        )
        return novel

    async def get_novels(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
        owner_id: Optional[int] = None,
    ) -> Tuple[List[Novel], int]:
        """
        查询小说列表（带分页、搜索与所有者过滤）。

        Args:
            db: 数据库会话
            skip: 跳过记录数（分页偏移）
            limit: 返回记录数（每页大小）
            search: 搜索关键词（匹配标题或作者）
            owner_id: 所有者用户ID（仅返回该用户的小说）

        Returns:
            (小说列表, 总数)
        """
        query = select(Novel).order_by(Novel.created_at.desc())
        count_query = select(func.count(Novel.id))

        # 所有者过滤
        if owner_id is not None:
            query = query.where(Novel.owner_id == owner_id)
            count_query = count_query.where(Novel.owner_id == owner_id)

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

        quarantined_path = None
        original_path = novel.source_path
        if original_path and os.path.exists(original_path):
            upload_dir = os.path.realpath(os.path.abspath(settings.upload_dir))
            real_source_path = os.path.realpath(os.path.abspath(original_path))
            if os.path.commonpath([upload_dir, real_source_path]) == upload_dir:
                quarantined_path = f"{real_source_path}.deleting-{secrets.token_hex(8)}"
                os.replace(real_source_path, quarantined_path)
            else:
                logger.error("拒绝处理上传目录外文件: %s", real_source_path)

        try:
            await db.delete(novel)
            await db.commit()
        except Exception:
            await db.rollback()
            if quarantined_path and original_path and os.path.exists(quarantined_path):
                os.replace(quarantined_path, original_path)
            raise

        if quarantined_path:
            try:
                os.remove(quarantined_path)
            except OSError:
                logger.exception(
                    "数据库删除成功，但隔离文件清理失败: %s", quarantined_path
                )
        logger.info(f"已删除小说: {novel.title} (ID={novel_id})")
        return True

    async def get_chapter(self, db: AsyncSession, chapter_id: int) -> Optional[Chapter]:
        """获取单个章节"""
        result = await db.execute(select(Chapter).where(Chapter.id == chapter_id))
        return result.scalar_one_or_none()

    async def update_reading_progress(
        self,
        db: AsyncSession,
        novel_id: int,
        chapter_id: int,
        progress_percent: float,
    ) -> Optional[dict]:
        """
        更新小说阅读进度。

        Args:
            db: 数据库会话
            novel_id: 小说ID
            chapter_id: 当前阅读到的章节ID
            progress_percent: 阅读进度百分比（0-100）

        Returns:
            更新后的进度信息，或 None（小说不存在）
        """
        from datetime import datetime

        novel = await self.get_novel(db, novel_id)
        if not novel:
            return None

        # 验证章节是否属于该小说
        chapter = await self.get_chapter(db, chapter_id)
        if not chapter or chapter.novel_id != novel_id:
            return None

        novel.reading_progress = {
            "chapter_id": chapter_id,
            "progress_percent": round(progress_percent, 2),
            "updated_at": datetime.now().isoformat(),
        }
        await db.flush()

        return {
            "novel_id": novel_id,
            "chapter_id": chapter_id,
            "progress_percent": novel.reading_progress["progress_percent"],
            "chapter_title": chapter.title,
            "updated_at": novel.reading_progress.get("updated_at"),
        }


# 全局单例
novel_service = NovelService()
