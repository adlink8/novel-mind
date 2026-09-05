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
from collections import deque
from typing import List, Optional, Tuple, Dict

import chardet
from fastapi import UploadFile
from sqlalchemy import select, func, text
from sqlalchemy.orm import noload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.novel import Novel, Chapter

logger = logging.getLogger(__name__)

# ────────────────────── 导入进度跟踪 ──────────────────────
# 内存中的导入状态跟踪（用于前端轮询进度）
# 格式: {novel_id: {"stage": "uploading", "percent": 10, "message": "..."}}
_import_status: Dict[int, dict] = {}

# 候选编码（按中文网文常见顺序）。禁止用 iso-8859-1 盲解中文小说（会“成功”成乱码）。
_ENCODING_CANDIDATES = (
    "utf-8-sig",
    "utf-8",
    "utf-16",  # 自动处理 BOM
    "utf-16-le",
    "utf-16-be",
    "gb18030",
    "gbk",
    "big5",
    "shift_jis",
    "euc-jp",
)

# 单章过大时按此字数切分（避免浏览器一次渲染数百万字卡死）
_MAX_CHAPTER_CHARS = 12_000
_MIN_CHAPTER_CHARS = 2_000

# 分页站每页重复的噪音行（如「铅笔小说 / (www.x23qb.com)」）
_WATERMARK_LINES = ("铅笔小说", "(www.x23qb.com)")
# 续页标题尾部的页码后缀（如「第一话 xxx(2/2)」）
_PAGE_SUFFIX_RE = re.compile(r"\s*[（(]\d+/\d+[)）]\s*$")
# 章节号核心（数字 + 单位字），供装饰性标题行归一化复用。
# 数字只认 ASCII：全角数字常见于正文列举（如「２、基司的搜索」），不作为章节号
_CH_NUMERALS = r"[零一二三四五六七八九十百千0-9]+"
_CH_UNIT = r"[章节回卷集篇部幕]"


def _cjk_ratio(text: str) -> float:
    """统计汉字（CJK Unified Ideographs）占比，用于编码打分。"""
    if not text:
        return 0.0
    sample = text if len(text) <= 50_000 else text[:50_000]
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return cjk / max(len(sample), 1)


def _replacement_ratio(text: str) -> float:
    if not text:
        return 1.0
    sample = text if len(text) <= 50_000 else text[:50_000]
    return sample.count("\ufffd") / max(len(sample), 1)


def _score_decoded_text(text: str) -> float:
    """
    对解码结果打分：越高越好。

    优先高汉字比例；惩罚替换符和“几乎无汉字”的长文本（典型乱码）。
    """
    if not text or not text.strip():
        return -1.0
    cjk = _cjk_ratio(text)
    repl = _replacement_ratio(text)
    # 可打印比例粗略估计
    sample = text[:20_000]
    printable = sum(1 for ch in sample if ch.isprintable() or ch in "\n\r\t") / max(
        len(sample), 1
    )
    score = cjk * 10.0 + printable * 0.5 - repl * 8.0
    # 长文却几乎无汉字 → 极可能编码错误
    if len(text) > 5_000 and cjk < 0.02:
        score -= 5.0
    return score


def _decode_with_fallback(raw: bytes) -> str:
    """
    用 BOM + chardet + 多编码候选打分，选择最像中文网文的解码结果。

    Raises:
        ValueError: 所有编码均无法得到可用文本
    """
    if not raw:
        raise ValueError("文件内容为空")

    candidates: list[tuple[str, str]] = []

    # 1) BOM 优先
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        for enc in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                candidates.append((enc, raw.decode(enc)))
            except UnicodeDecodeError:
                continue
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            candidates.append(("utf-8-sig", raw.decode("utf-8-sig")))
        except UnicodeDecodeError:
            pass

    # 2) chardet 提示
    sample = raw if len(raw) <= 200_000 else raw[:200_000]
    detected = chardet.detect(sample)
    det_enc = (detected.get("encoding") or "").lower().replace("_", "-")
    det_conf = float(detected.get("confidence") or 0)
    if det_enc in ("gb2312", "gbk"):
        det_enc = "gb18030"
    if det_enc in ("utf-16le",):
        det_enc = "utf-16-le"
    if det_enc in ("utf-16be",):
        det_enc = "utf-16-be"
    logger.info("chardet 检测: %s (置信度 %.2f)", det_enc or "?", det_conf)

    ordered: list[str] = []
    # chardet 对文库 TXT 常误判为 cp1006 等（置信度很低），低置信度结果不优先
    if det_enc and det_conf >= 0.55:
        ordered.append(det_enc)
    for enc in _ENCODING_CANDIDATES:
        if enc not in ordered:
            ordered.append(enc)
    # 低置信度检测结果仍放入候选末尾，靠打分淘汰乱码
    if det_enc and det_enc not in ordered:
        ordered.append(det_enc)

    for enc in ordered:
        try:
            # 中文网文禁用 errors=replace，避免静默吞错误
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        candidates.append((enc, text))

    if not candidates:
        raise ValueError("无法识别文件编码，请使用 UTF-8 / GBK / UTF-16 编码的 TXT")

    best_enc, best_text, best_score = "", "", -1e9
    for enc, text in candidates:
        score = _score_decoded_text(text)
        if score > best_score:
            best_enc, best_text, best_score = enc, text, score

    cjk = _cjk_ratio(best_text)
    logger.info(
        "选用编码 %s (score=%.2f, cjk_ratio=%.3f, chars=%d)",
        best_enc,
        best_score,
        cjk,
        len(best_text),
    )
    if len(best_text) > 20_000 and cjk < 0.01:
        raise ValueError(
            "文本解码后几乎不含中文，疑似编码错误。请将文件另存为 UTF-8 或 GBK 后再上传"
        )
    return best_text


# ────────────────────── 章节分割正则模式 ──────────────────────
# 5 种模式按优先级排列，覆盖中英文常见章节标题格式
# 句读守卫：真标题几乎不会以句读结尾，而行首恰好是「数字+回/卷/部…」的正文句子几乎都会
# （如「一回想起…」「第二回合要开始了。」「1.召唤「无机物」。」）
_SENTENCE_TAIL_GUARD = r"(?!.*[。！？…；，.!?,;]$)"

CHAPTER_PATTERNS = [
    rf"^第{_CH_NUMERALS}{_CH_UNIT}{_SENTENCE_TAIL_GUARD}.*$",  # 中文章节号（"第一章 xxx"）
    rf"^[第]?{_CH_NUMERALS}{_CH_UNIT}{_SENTENCE_TAIL_GUARD}.{{0,50}}$",  # 宽松中文章节（可省略"第"，限长防误伤长句）
    r"^Chapter\s+\d+.*$",  # 英文 Chapter（"Chapter 1 xxx"）
    r"^CHAPTER\s+\d+.*$",  # 大写 CHAPTER
    # 数字标题（"1. xxx" 或 "1、xxx"）；分隔符只允许点/顿号/同行空格，
    # 不能用 \s（会跨行把「004」这类独立页码行与下一行拼成标题）
    rf"^[0-9]+(?:[\.、]|[^\S\n]){_SENTENCE_TAIL_GUARD}.+$",
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
        4. 归一化装饰性标题行（「# 第X话」/「_第X话_」）
        5. 用正则匹配章节标题位置，合并同标题续页（尾部带 (n/m) 页码）
        6. 剔除分页水印行与重复标题行，按标题位置切分文本
        7. 计算每章字数
        """
        # 基础清洗
        content = content.lstrip("\ufeff")  # 去除 UTF-8 BOM
        content = content.replace("\r\n", "\n").replace("\r", "\n")  # 统一换行符
        content = re.sub(r"\n{3,}", "\n\n", content)  # 多个空行合并为两个

        # 归一化装饰性标题行：文库站导出常见「# 第X话」Markdown 前缀与「_第X话_」下划线包裹。
        # 仅当行核心本身是章节号模式时才剥离装饰，避免误伤正文里引用章节号的行。
        content = re.sub(rf"(?m)^#{{1,6}}\s+(?=第{_CH_NUMERALS}{_CH_UNIT})", "", content)
        content = re.sub(rf"(?m)^_+(第{_CH_NUMERALS}{_CH_UNIT}.*?)_+\s*$", r"\1", content)

        # 查找所有章节标题位置
        matches = list(CHAPTER_REGEX.finditer(content))

        chapters: List[dict] = []

        if not matches:
            # 未检测到章节标记 → 按固定字数切分，避免「全文一章」卡死阅读器
            logger.info("未检测到章节标记，按约 %d 字切分全文", _MAX_CHAPTER_CHARS)
            chapters = self._split_by_size(content.strip(), title_prefix="第")
        else:
            # 合并同一章节的续页：分页站每页重复章节标题，续页标题尾部带 (n/m) 页码。
            # 比对时忽略空白差异（源文件常见「茱丽叶特 •礼仪」与「茱丽叶特•礼仪」两种排法）
            headings: List[Tuple[int, str, str]] = []
            for m in matches:
                title = _PAGE_SUFFIX_RE.sub("", m.group()).strip()
                if not title:
                    continue
                key = re.sub(r"\s+", "", title)
                if headings and headings[-1][2] == key:
                    continue
                headings.append((m.start(), title, key))

            # 处理第一个章节标题前的前言部分（如有）
            preamble = content[: headings[0][0]].strip()
            if preamble and len(preamble) > 100:  # 前言超过 100 字才单独成章
                chapters.append(
                    {
                        "chapter_number": 0,
                        "title": "前言",
                        "content": preamble,
                    }
                )

            # 按标题位置切分各章节
            for idx, (start, title_line, _key) in enumerate(headings):
                end = (
                    headings[idx + 1][0] if idx + 1 < len(headings) else len(content)
                )

                title = title_line[:100]  # 截断过长标题
                body = self._strip_page_noise(content[start:end], title_line)

                chapters.append(
                    {
                        "chapter_number": idx + 1,
                        "title": title,
                        "content": body,
                    }
                )

            # 标题识别成功但单章过大（正则过粗）→ 二次按字数切开
            chapters = self._split_oversized_chapters(chapters)

        # 计算字数并顺序编号（前言保持 0）
        next_num = 1
        for ch in chapters:
            ch["word_count"] = len(ch.get("content") or "")
            if ch.get("title") == "前言" and ch.get("chapter_number") == 0:
                continue
            ch["chapter_number"] = next_num
            next_num += 1

        logger.info(f"章节分割完成: 共 {len(chapters)} 章")
        return chapters

    @staticmethod
    def _strip_page_noise(body: str, title: str) -> str:
        """剔除分页模板噪音：站点水印行与每页重复的章节标题行（含 (n/m) 续页标头）。

        标题比对忽略空白差异；首处标题行保留，维持「章节正文以标题行开头」的既有行为。
        """
        title_key = re.sub(r"\s+", "", title)
        title_seen = False
        kept: List[str] = []
        for line in body.split("\n"):
            s = line.strip()
            if s in _WATERMARK_LINES:
                continue
            if s and re.sub(r"\s+", "", _PAGE_SUFFIX_RE.sub("", s)) == title_key:
                if title_seen:
                    continue
                title_seen = True
            kept.append(line)
        return "\n".join(kept).strip()

    def _split_by_size(self, content: str, title_prefix: str = "分段") -> List[dict]:
        """将无标题长文按段落边界切成可读小段。"""
        text = content.strip()
        if not text:
            return [{"chapter_number": 1, "title": f"{title_prefix}1", "content": ""}]
        if len(text) <= _MAX_CHAPTER_CHARS:
            return [
                {
                    "chapter_number": 1,
                    "title": f"{title_prefix}1（全文）",
                    "content": text,
                }
            ]

        chunks: List[str] = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + _MAX_CHAPTER_CHARS, n)
            if end < n:
                # 优先在段落边界切开
                window = text[start:end]
                split_at = max(window.rfind("\n\n"), window.rfind("\n"))
                if split_at >= _MIN_CHAPTER_CHARS:
                    end = start + split_at + 1
            piece = text[start:end].strip()
            if piece:
                chunks.append(piece)
            start = end if end > start else start + _MAX_CHAPTER_CHARS

        return [
            {
                "chapter_number": i + 1,
                "title": f"{title_prefix}{i + 1}",
                "content": piece,
            }
            for i, piece in enumerate(chunks)
        ]

    def _split_oversized_chapters(self, chapters: List[dict]) -> List[dict]:
        """将超过阈值的章再切分，防止单章百万字。"""
        result: List[dict] = []
        for ch in chapters:
            body = ch.get("content") or ""
            if len(body) <= _MAX_CHAPTER_CHARS * 2:
                result.append(ch)
                continue
            title = ch.get("title") or "章节"
            parts = self._split_by_size(body, title_prefix=f"{title}·")
            for i, part in enumerate(parts):
                part["title"] = (
                    title if len(parts) == 1 else f"{title}（{i + 1}/{len(parts)}）"
                )
                result.append(part)
        return result

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

        # A novel is not runtime-available until its durable built-in Skill
        # defaults exist.  The activation service reads the authoritative
        # agent-service packages and is idempotent for imports/retries.
        from app.services.agent_runtime.registry import ensure_builtin_skills

        await ensure_builtin_skills(db, owner_id=owner_id, novel_id=novel.id)

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
        query = (
            select(Novel)
            .options(noload(Novel.chapters))
            .order_by(Novel.created_at.desc())
        )
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

    async def update_novel(self, db: AsyncSession, novel: Novel, values: dict) -> Novel:
        """更新已完成所有权校验的小说元信息。"""
        for field, value in values.items():
            if field == "title" and isinstance(value, str):
                value = value.strip()
                if not value:
                    raise ValueError("小说名称不能为空")
            setattr(novel, field, value)
        await db.commit()
        await db.refresh(novel)
        return novel

    async def delete_novels(
        self,
        db: AsyncSession,
        novel_ids: List[int],
        *,
        owner_id: Optional[int],
    ) -> tuple[List[int], List[int]]:
        """按所有者批量删除；超级用户传 ``owner_id=None``。"""
        unique_ids = list(dict.fromkeys(novel_ids))
        query = select(Novel).where(Novel.id.in_(unique_ids))
        if owner_id is not None:
            query = query.where(Novel.owner_id == owner_id)
        result = await db.execute(query)
        owned = {novel.id: novel for novel in result.scalars().all()}

        deleted_ids: List[int] = []
        for novel_id in unique_ids:
            if novel_id not in owned:
                continue
            if await self.delete_novel(db, novel_id):
                deleted_ids.append(novel_id)
        skipped_ids = [
            novel_id for novel_id in unique_ids if novel_id not in deleted_ids
        ]
        return deleted_ids, skipped_ids

    async def delete_novel(self, db: AsyncSession, novel_id: int) -> bool:
        """
        删除小说及其所有关联数据（含分析产物与审计数据）。

        行为:
        1. 删除源文件（uploads 目录下的 TXT，先隔离再清理）
        2. Postgres：临时禁用 append-only 审计触发器，按拓扑序显式清空
           所有 novel 作用域数据（版本链、证据引用等 RESTRICT 外键使级联
           顺序不可控），再删除 chapters/novels 让级联收尾，最后恢复触发器
        3. SQLite（测试）：依赖模型声明的 ORM/数据库级联
        4. 提交事务

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
            if db.bind.dialect.name == "postgresql":
                # 审计表的 append-only 触发器与 RESTRICT 外键会拦截级联删除，
                # 需先禁用触发器、按拓扑序显式清空 novel 作用域数据，再删本体。
                # 事务性 DDL：回滚时触发器禁用自动随之撤销。
                guard_tables = await self._disable_append_only_triggers(db)
                try:
                    await self._purge_novel_scoped_data(db, novel_id)
                    await db.delete(novel)
                    await self._enable_append_only_triggers(db, guard_tables)
                except Exception:
                    await db.rollback()
                    raise
            else:
                # SQLite（测试环境）：无 pg_catalog 与审计触发器，依赖模型级联
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

    # ── Postgres 删除辅助：审计触发器与 novel 作用域数据清理 ──

    @staticmethod
    async def _disable_append_only_triggers(db: AsyncSession) -> List[str]:
        """临时禁用所有 append-only 审计触发器，返回受影响的表名列表。

        这些触发器无条件拒绝 DELETE/UPDATE（防止业务代码误删审计数据），
        小说删除是唯一经用户二次确认的显式清理入口，故在此临时放行。
        仅在删除事务内生效：提交前恢复；回滚时由事务性 DDL 自动恢复。
        """
        rows = await db.execute(
            text(
                "SELECT DISTINCT tgrelid::regclass::text AS tbl "
                "FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE NOT t.tgisinternal AND ("
                "p.prosrc ILIKE '%RAISE EXCEPTION%' OR p.prosrc ILIKE '%RETURN NULL%')"
            )
        )
        tables = sorted({r[0] for r in rows})
        for tbl in tables:
            await db.execute(text(f'ALTER TABLE "{tbl}" DISABLE TRIGGER USER'))
        return tables

    @staticmethod
    async def _enable_append_only_triggers(
        db: AsyncSession, tables: List[str]
    ) -> None:
        """恢复 append-only 审计触发器（须在提交前调用）。"""
        for tbl in tables:
            await db.execute(text(f'ALTER TABLE "{tbl}" ENABLE TRIGGER USER'))

    @staticmethod
    async def _purge_novel_scoped_data(db: AsyncSession, novel_id: int) -> None:
        """按「子表先删」的拓扑序清空所有以 novel_id 为作用域的数据。

        ORM 仅声明了部分 relationship，其余表依赖数据库级联；但 RESTRICT 外键
        （版本链、证据引用链）的级联顺序不可控。这里从 pg_catalog 动态收集
        novel 作用域表并排序执行，新增模型无需维护清单。
        chapters/novels 本体由调用方处理；无 novel_id 的子表由父表级联收尾
        （此时 append-only 触发器已禁用，级联可正常进行）。
        """
        table_rows = await db.execute(
            text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = c.oid "
                "WHERE c.relkind = 'r' AND n.nspname = 'public' "
                "AND a.attname = 'novel_id' AND NOT a.attisdropped"
            )
        )
        tables = {r[0] for r in table_rows} - {"novels", "chapters"}
        if not tables:
            return

        tbl_list = ", ".join(f"'{t}'" for t in sorted(tables))

        # 前置：无 novel_id 列但 RESTRICT 引用作用域表的子表，须先经父路径清空
        # （reader_message_citations 经 reader_messages 作用域，RESTRICT 指向
        #  knowledge_evidence_refs / reader_context_evidence_refs）
        await db.execute(
            text(
                "DELETE FROM reader_message_citations WHERE assistant_message_id IN "
                "(SELECT id FROM reader_messages WHERE novel_id = :nid)"
            ),
            {"nid": novel_id},
        )

        # 表间外键边（child -> parent）。CASCADE/NO ACTION 边同样参与排序：
        # 删除父表会级联带走子表，若子表的 RESTRICT 引用方尚未清理则会爆炸
        edge_rows = await db.execute(
            text(
                "SELECT conrelid::regclass::text AS child, "
                "confrelid::regclass::text AS parent, confdeltype, "
                "(SELECT array_agg(a.attname ORDER BY k.ord) "
                " FROM unnest(conkey) WITH ORDINALITY AS k(attnum, ord) "
                " JOIN pg_attribute a ON a.attrelid = conrelid AND a.attnum = k.attnum"
                ") AS child_cols, "
                "(SELECT bool_and(NOT a.attnotnull) "
                " FROM unnest(conkey) AS k(attnum) "
                " JOIN pg_attribute a ON a.attrelid = conrelid AND a.attnum = k.attnum"
                ") AS child_nullable "
                "FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid <> confrelid "
                f"AND conrelid::regclass::text IN ({tbl_list}) "
                f"AND confrelid::regclass::text IN ({tbl_list}, 'chapters', 'novels')"
            )
        )
        edges = []
        for r in edge_rows:
            # asyncpg 对 char 类型返回 bytes（b'n'），统一转 str
            ftype = r[2].decode() if isinstance(r[2], bytes) else r[2]
            edges.append((r[0], r[1], ftype, list(r[3] or []), bool(r[4])))

        # 自引用 RESTRICT（如 narrative_memory_versions 版本链）无法靠语句间顺序，
        # 需按「无子引用的叶子优先」逐层删除
        self_rows = await db.execute(
            text(
                "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) "
                "FROM pg_constraint WHERE contype = 'f' AND conrelid = confrelid "
                "AND confdeltype = 'r' "
                f"AND conrelid::regclass::text IN ({tbl_list})"
            )
        )
        self_restrict = {}
        for tbl, defn in self_rows:
            # 形如 FOREIGN KEY (owner_id, novel_id, parent_version_id)
            # REFERENCES tbl(owner_id, novel_id, id) ON DELETE RESTRICT
            child_cols = [
                c.strip()
                for c in defn.split("FOREIGN KEY (")[1].split(")")[0].split(",")
            ]
            ref_cols = [
                c.strip()
                for c in defn.split("REFERENCES ")[1].split("(")[1].split(")")[0].split(",")
            ]
            self_restrict[tbl] = " AND ".join(
                f'c."{cc}" = v."{rc}"' for cc, rc in zip(child_cols, ref_cols)
            )

        def kahn(pending: set, skip: set = frozenset()) -> list:
            """子表先删的拓扑序；返回能求解的部分。skip 为需剔除的边下标。"""
            blockers = {t: 0 for t in pending}
            parents_of = {t: [] for t in pending}
            for i, (child, parent, _t, _c, _n) in enumerate(edges):
                if i in skip:
                    continue
                if child in pending and parent in pending:
                    blockers[parent] += 1
                    parents_of[child].append(parent)
            order: List[str] = []
            queue = deque(sorted(t for t in pending if blockers[t] == 0))
            while queue:
                t = queue.popleft()
                order.append(t)
                for p in parents_of[t]:
                    blockers[p] -= 1
                    if blockers[p] == 0:
                        queue.append(p)
            return order

        order = kahn(tables)
        leftover = tables - set(order)
        if leftover:
            # 环（如 artifacts ↔ artifact_revisions 互引）：先断开环内可空的
            # NO ACTION 引用列（数据置 NULL），并把对应边从图中剔除后再求解。
            # 断开的行随本事务一并删除。
            broken: set = set()
            for i, (child, parent, ftype, cols, nullable) in enumerate(edges):
                if (
                    child in leftover
                    and parent in leftover
                    and ftype in ("n", "a")
                    and nullable
                    and cols
                ):
                    broken.add(i)
                    set_clause = ", ".join(f'"{c}" = NULL' for c in cols)
                    await db.execute(
                        text(f'UPDATE "{child}" SET {set_clause} WHERE novel_id = :nid'),
                        {"nid": novel_id},
                    )
            resolved = kahn(leftover, skip=broken)
            order.extend(resolved)
            still_stuck = tables - set(order)
            if still_stuck:  # RESTRICT 真环：理论不存在，兜底按名续删（会报错暴露）
                order.extend(sorted(still_stuck))
        logger.debug("小说 %s 清理顺序: %s", novel_id, order)

        total = 0
        per_table: List[Tuple[str, int]] = []
        for tbl in order:
            rows_deleted = 0
            if tbl in self_restrict:
                while True:
                    r = await db.execute(
                        text(
                            f'DELETE FROM "{tbl}" WHERE id IN ('
                            f'SELECT v.id FROM "{tbl}" v WHERE v.novel_id = :nid '
                            f"AND NOT EXISTS (SELECT 1 FROM \"{tbl}\" c "
                            f"WHERE {self_restrict[tbl]}))"
                        ),
                        {"nid": novel_id},
                    )
                    if not r.rowcount:
                        break
                    rows_deleted += r.rowcount
            else:
                r = await db.execute(
                    text(f'DELETE FROM "{tbl}" WHERE novel_id = :nid'),
                    {"nid": novel_id},
                )
                rows_deleted = r.rowcount
            total += rows_deleted
            per_table.append((tbl, rows_deleted))
            logger.debug("purge %s: %d 行", tbl, rows_deleted)
        logger.info(
            "已清理小说 %s 的作用域数据（%d 张表，%d 行）: %s",
            novel_id,
            len(order),
            total,
            per_table,
        )

    async def get_chapter(self, db: AsyncSession, chapter_id: int) -> Optional[Chapter]:
        """获取单个章节（显式加载 deferred 正文）"""
        from sqlalchemy.orm import undefer

        result = await db.execute(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(Chapter.id == chapter_id)
        )
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
