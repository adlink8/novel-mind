"""
文本分块服务

将小说章节内容分割成语义块，用于 RAG 检索。

分块策略：
  - 目标块大小：300-500 字（可配置）
  - 按自然段落分割，合并过短的段落
  - 识别块类型：scene / dialogue / description / narration / paragraph

块类型检测规则：
  - dialogue:   引号密度 > 30% 的连续对话段落
  - description: 包含描写性关键词（风景、外貌、心理等）
  - scene:      场景转换标记（时间/地点变化）
  - narration:  旁白/背景介绍
  - paragraph:  默认类型
"""

import re
from dataclasses import dataclass


@dataclass
class Chapter:
    """章节数据结构（用于 chunk_novel 接口）"""

    id: int
    chapter_number: int
    content: str


# 描写性关键词（风景、外貌、心理、动作等）
DESCRIPTION_KEYWORDS = [
    # 风景描写
    "风景", "景色", "山川", "河流", "天空", "云彩", "日落", "日出",
    "月光", "星光", "花开", "花落", "落叶", "微风", "细雨", "白雪",
    "碧绿", "金黄", "蔚蓝", "苍茫", "寂静", "幽深",
    # 外貌描写
    "容貌", "面容", "眉眼", "眼眸", "嘴唇", "脸颊", "身姿", "体态",
    "秀发", "乌发", "白皙", "俊美", "清秀", "妩媚", "英俊", "潇洒",
    # 心理描写
    "心中", "内心", "心想", "暗想", "思绪", "心情", "感慨", "感叹",
    "忧虑", "担忧", "欢喜", "悲伤", "愤怒", "惊讶", "恐惧", "不安",
    "欣慰", "惆怅", "怀念", "向往", "期待", "失望",
    # 动作描写
    "缓缓", "轻轻", "默默", "静静", "慢慢", "急忙", "匆忙", "悄然",
    "伫立", "凝视", "眺望", "俯身", "抬头", "转身", "回眸", "微笑",
]

# 场景转换标记
SCENE_MARKERS = [
    # 时间变化
    "翌日", "次日", "翌晨", "清晨", "黄昏", "傍晚", "深夜", "午夜",
    "数日后", "几日后", "半月后", "一月后", "半年后", "一年后",
    "时光飞逝", "光阴似箭", "岁月如梭",
    # 地点变化
    "来到", "走进", "走出", "进入", "离开", "抵达", "到达",
    "回到", "返回", "前往", "赶往", "奔赴",
]

# 对话引号模式
QUOTE_PATTERNS = [
    re.compile(r'["""]'),  # 中文引号和英文引号
    re.compile(r'[「」]'),  # 日文引号
]


class ChunkingService:
    """
    文本分块服务

    将小说章节内容分割成语义块，用于 RAG 检索和向量化。
    """

    def __init__(self, min_chunk_size: int = 300, max_chunk_size: int = 500):
        """
        初始化分块服务

        Args:
            min_chunk_size: 最小块大小（字数），默认 300
            max_chunk_size: 最大块大小（字数），默认 500
        """
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size

    async def chunk_chapter(
        self, chapter_id: int, chapter_number: int, content: str
    ) -> list[dict]:
        """
        将单个章节内容分割成语义块

        Args:
            chapter_id: 章节 ID
            chapter_number: 章节序号
            content: 章节内容

        Returns:
            块列表，每个块包含：
            - content: 块内容
            - chunk_type: 块类型
            - chunk_index: 块序号（从 0 开始）
            - word_count: 字数
            - metadata_json: 元数据
        """
        if not content or not content.strip():
            return []

        # 分割成段落
        paragraphs = self._split_into_paragraphs(content)
        if not paragraphs:
            return []

        # 合并段落成块
        chunks = []
        current_chunk_parts = []
        current_chunk_size = 0

        for paragraph in paragraphs:
            paragraph_size = len(paragraph)

            # 如果当前块加上新段落会超过最大大小，且当前块已有内容，则保存当前块
            if (
                current_chunk_size + paragraph_size > self.max_chunk_size
                and current_chunk_parts
            ):
                chunk_content = "\n".join(current_chunk_parts)
                chunks.append(chunk_content)
                current_chunk_parts = []
                current_chunk_size = 0

            # 如果单个段落超过最大大小，需要进一步分割
            if paragraph_size > self.max_chunk_size:
                # 先保存当前已积累的块
                if current_chunk_parts:
                    chunk_content = "\n".join(current_chunk_parts)
                    chunks.append(chunk_content)
                    current_chunk_parts = []
                    current_chunk_size = 0

                # 分割超长段落
                sub_chunks = self._split_long_paragraph(paragraph)
                chunks.extend(sub_chunks)
            else:
                current_chunk_parts.append(paragraph)
                current_chunk_size += paragraph_size

        # 保存最后一块
        if current_chunk_parts:
            chunk_content = "\n".join(current_chunk_parts)
            # 如果最后一块太小，尝试与前一块合并
            if len(chunk_content) < self.min_chunk_size and chunks:
                last_chunk = chunks.pop()
                chunk_content = last_chunk + "\n" + chunk_content
            chunks.append(chunk_content)

        # 构建结果
        result = []
        for index, chunk_content in enumerate(chunks):
            chunk_type = self._detect_chunk_type(chunk_content)
            word_count = len(chunk_content)
            metadata = {
                "chapter_id": chapter_id,
                "chapter_number": chapter_number,
                "position_in_chapter": index / max(len(chunks) - 1, 1),
            }
            result.append(
                {
                    "content": chunk_content,
                    "chunk_type": chunk_type,
                    "chunk_index": index,
                    "word_count": word_count,
                    "metadata_json": metadata,
                }
            )

        return result

    async def chunk_novel(
        self, novel_id: int, chapters: list[Chapter]
    ) -> list[dict]:
        """
        对整本小说的所有章节进行分块

        Args:
            novel_id: 小说 ID
            chapters: 章节列表

        Returns:
            所有块的列表
        """
        all_chunks = []
        for chapter in chapters:
            chapter_chunks = await self.chunk_chapter(
                chapter_id=chapter.id,
                chapter_number=chapter.chapter_number,
                content=chapter.content,
            )
            all_chunks.extend(chapter_chunks)
        return all_chunks

    def _detect_chunk_type(self, text: str) -> str:
        """
        检测文本块的类型

        检测规则（按优先级）：
        1. dialogue: 引号密度 > 30%
        2. scene: 包含场景转换标记
        3. description: 包含描写性关键词
        4. paragraph: 默认类型

        Args:
            text: 文本内容

        Returns:
            块类型字符串
        """
        if not text:
            return "paragraph"

        # 计算引号密度
        quote_chars = 0
        for pattern in QUOTE_PATTERNS:
            quote_chars += len(pattern.findall(text))

        # 引号密度 > 15% 判定为对话
        if len(text) > 0 and quote_chars / len(text) > 0.15:
            return "dialogue"

        # 检测场景转换标记
        for marker in SCENE_MARKERS:
            if marker in text:
                return "scene"

        # 检测描写性关键词
        description_count = sum(1 for kw in DESCRIPTION_KEYWORDS if kw in text)
        if description_count >= 3:
            return "description"

        # 默认返回 paragraph
        return "paragraph"

    def _split_into_paragraphs(self, content: str) -> list[str]:
        """
        将内容分割成段落

        处理规则：
        1. 按换行符分割
        2. 过滤空行
        3. 合并过短的段落（< 50 字）

        Args:
            content: 原始内容

        Returns:
            段落列表
        """
        if not content:
            return []

        # 统一换行符
        content = content.replace("\r\n", "\n").replace("\r", "\n")

        # 按换行符分割
        raw_paragraphs = content.split("\n")

        # 过滤空行并去除首尾空白
        paragraphs = []
        for p in raw_paragraphs:
            stripped = p.strip()
            if stripped:
                paragraphs.append(stripped)

        if not paragraphs:
            return []

        # 合并过短的段落（< 50 字）
        merged_paragraphs = []
        current_paragraph = ""

        for paragraph in paragraphs:
            if not current_paragraph:
                current_paragraph = paragraph
            elif len(current_paragraph) < 50:
                # 短段落与下一段合并
                current_paragraph = current_paragraph + "\n" + paragraph
            else:
                merged_paragraphs.append(current_paragraph)
                current_paragraph = paragraph

        # 添加最后一段
        if current_paragraph:
            merged_paragraphs.append(current_paragraph)

        return merged_paragraphs

    def _split_long_paragraph(self, paragraph: str) -> list[str]:
        """
        分割超长段落

        按句子边界分割，保持语义完整性。

        Args:
            paragraph: 超长段落

        Returns:
            分割后的块列表
        """
        # 按中文句子结束符分割
        sentences = re.split(r'([。！？；…]+)', paragraph)

        chunks = []
        current_chunk = ""

        for i in range(0, len(sentences), 2):
            sentence = sentences[i]
            # 句号等标点符号在奇数位
            if i + 1 < len(sentences):
                sentence += sentences[i + 1]

            if len(current_chunk) + len(sentence) > self.max_chunk_size:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sentence
            else:
                current_chunk += sentence

        if current_chunk:
            chunks.append(current_chunk)

        return chunks


# 单例实例
chunking_service = ChunkingService()
