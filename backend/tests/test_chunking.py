"""
ChunkingService 单元测试

覆盖范围:
- 基本分块功能
- 对话类型检测
- 段落合并（短段落）
- 空内容处理
- 超长段落分割
- chunk_index 连续性
- word_count 计算

注意:
- 不依赖数据库，纯单元测试
- 直接实例化 ChunkingService 测试分块逻辑
"""

import pytest

from app.services.chunking_service import ChunkingService, Chapter


@pytest.fixture
def chunking_service():
    """创建分块服务实例"""
    return ChunkingService()


class TestChunkChapter:
    """测试 chunk_chapter 的基本分块功能"""

    @pytest.mark.asyncio
    async def test_basic_chunking(self, chunking_service: ChunkingService):
        """基本分块功能：将内容分割成多个块"""
        content = "这是一段测试内容。" * 50  # 约 400 字
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=content
        )
        assert len(chunks) >= 1
        assert all("content" in chunk for chunk in chunks)
        assert all("chunk_type" in chunk for chunk in chunks)
        assert all("chunk_index" in chunk for chunk in chunks)
        assert all("word_count" in chunk for chunk in chunks)
        assert all("metadata_json" in chunk for chunk in chunks)

    @pytest.mark.asyncio
    async def test_empty_content(self, chunking_service: ChunkingService):
        """空内容返回空列表"""
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=""
        )
        assert chunks == []

    @pytest.mark.asyncio
    async def test_none_content(self, chunking_service: ChunkingService):
        """None 内容返回空列表"""
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=None
        )
        assert chunks == []

    @pytest.mark.asyncio
    async def test_whitespace_content(self, chunking_service: ChunkingService):
        """纯空白内容返回空列表"""
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content="   \n\n  "
        )
        assert chunks == []

    @pytest.mark.asyncio
    async def test_chunk_index_continuity(self, chunking_service: ChunkingService):
        """chunk_index 连续性：从 0 开始递增"""
        content = "这是测试内容。" * 200  # 足够长的内容
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=content
        )
        indices = [chunk["chunk_index"] for chunk in chunks]
        assert indices == list(range(len(chunks)))

    @pytest.mark.asyncio
    async def test_word_count_accuracy(self, chunking_service: ChunkingService):
        """word_count 计算准确"""
        content = "这是一段测试内容。"
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=content
        )
        assert len(chunks) == 1
        assert chunks[0]["word_count"] == len(content)

    @pytest.mark.asyncio
    async def test_metadata_structure(self, chunking_service: ChunkingService):
        """metadata_json 结构正确"""
        content = "测试内容。" * 50
        chunks = await chunking_service.chunk_chapter(
            chapter_id=42, chapter_number=7, content=content
        )
        for chunk in chunks:
            metadata = chunk["metadata_json"]
            assert metadata["chapter_id"] == 42
            assert metadata["chapter_number"] == 7
            assert "position_in_chapter" in metadata


class TestDialogueDetection:
    """测试对话类型检测"""

    @pytest.mark.asyncio
    async def test_dialogue_detection(self, chunking_service: ChunkingService):
        """对话类型检测：引号密度 > 30%"""
        # 构造高密度对话内容
        dialogue_lines = [
            '"你好啊！"',
            '"我很好，谢谢。"',
            '"今天天气真不错。"',
            '"是啊，阳光明媚。"',
            '"我们去散步吧。"',
            '"好主意，走吧。"',
        ]
        content = "\n".join(dialogue_lines)
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=content
        )
        assert len(chunks) >= 1
        assert chunks[0]["chunk_type"] == "dialogue"

    @pytest.mark.asyncio
    async def test_japanese_quotes(self, chunking_service: ChunkingService):
        """日文引号也能检测为对话"""
        dialogue_lines = [
            "「你好啊！」",
            "「我很好，谢谢。」",
            "「今天天气真不错。」",
            "「是啊，阳光明媚。」",
        ]
        content = "\n".join(dialogue_lines)
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=content
        )
        assert chunks[0]["chunk_type"] == "dialogue"


class TestDescriptionDetection:
    """测试描写类型检测"""

    @pytest.mark.asyncio
    async def test_description_detection(self, chunking_service: ChunkingService):
        """描写类型检测：包含足够描写性关键词"""
        content = (
            "她有着秀发乌黑亮丽，眼眸清澈如水，面容清秀脱俗。"
            "心中暗想，这风景真是美不胜收。"
            "山川壮丽，河流蜿蜒，天空蔚蓝。"
        )
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=content
        )
        assert chunks[0]["chunk_type"] == "description"


class TestParagraphMerging:
    """测试段落合并功能"""

    @pytest.mark.asyncio
    async def test_short_paragraph_merging(self, chunking_service: ChunkingService):
        """短段落合并：连续短段落应合并"""
        # 构造多个短段落（每个 < 50 字）
        paragraphs = ["短段落一。", "短段落二。", "短段落三。", "这是一个稍长一些的段落，超过五十个字的限制。" * 2]
        content = "\n".join(paragraphs)
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=content
        )
        # 短段落应该被合并
        assert len(chunks) <= len(paragraphs)

    @pytest.mark.asyncio
    async def test_long_paragraph_not_merged(self, chunking_service: ChunkingService):
        """长段落不合并"""
        # 构造一个长段落（> 50 字）
        long_paragraph = "这是一个很长的段落，包含了足够的内容，不会被与其他段落合并。" * 10
        content = long_paragraph
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=content
        )
        assert len(chunks) >= 1


class TestLongParagraphSplitting:
    """测试超长段落分割"""

    @pytest.mark.asyncio
    async def test_long_paragraph_splitting(self, chunking_service: ChunkingService):
        """超长段落分割：超过 max_chunk_size 应被分割"""
        # 构造超长内容（无换行符）
        content = "这是一句话。" * 200  # 约 1200 字
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=content
        )
        assert len(chunks) > 1
        # 每块不应超过 max_chunk_size 太多（允许句子完整性）
        for chunk in chunks:
            assert chunk["word_count"] <= chunking_service.max_chunk_size + 100

    @pytest.mark.asyncio
    async def test_sentence_boundary_splitting(self, chunking_service: ChunkingService):
        """按句子边界分割"""
        content = "第一句话。第二句话！第三句话？第四句话；第五句话。" * 50
        chunks = await chunking_service.chunk_chapter(
            chapter_id=1, chapter_number=1, content=content
        )
        # 检查块内容不截断句子
        for chunk in chunks:
            content = chunk["content"]
            # 不应以标点符号开头（除了引号）
            if content and content[0] not in '""「':
                assert content[0] not in '。！？；'


class TestChunkNovel:
    """测试 chunk_novel 整本小说分块"""

    @pytest.mark.asyncio
    async def test_chunk_novel(self, chunking_service: ChunkingService):
        """整本小说分块"""
        chapters = [
            Chapter(id=1, chapter_number=1, content="第一章内容。" * 100),
            Chapter(id=2, chapter_number=2, content="第二章内容。" * 100),
            Chapter(id=3, chapter_number=3, content="第三章内容。" * 100),
        ]
        chunks = await chunking_service.chunk_novel(novel_id=1, chapters=chapters)
        assert len(chunks) > 0
        # 验证所有章节都被处理
        chapter_ids = set(chunk["metadata_json"]["chapter_id"] for chunk in chunks)
        assert chapter_ids == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_chunk_novel_empty_chapters(self, chunking_service: ChunkingService):
        """空章节列表返回空列表"""
        chunks = await chunking_service.chunk_novel(novel_id=1, chapters=[])
        assert chunks == []


class TestChunkTypeDetection:
    """测试 _detect_chunk_type 方法"""

    def test_default_type(self, chunking_service: ChunkingService):
        """默认类型为 paragraph"""
        chunk_type = chunking_service._detect_chunk_type("普通文本内容")
        assert chunk_type == "paragraph"

    def test_empty_text(self, chunking_service: ChunkingService):
        """空文本返回 paragraph"""
        chunk_type = chunking_service._detect_chunk_type("")
        assert chunk_type == "paragraph"

    def test_scene_detection_time(self, chunking_service: ChunkingService):
        """时间变化检测为 scene"""
        chunk_type = chunking_service._detect_chunk_type("翌日清晨，他早早起床。")
        assert chunk_type == "scene"

    def test_scene_detection_location(self, chunking_service: ChunkingService):
        """地点变化检测为 scene"""
        chunk_type = chunking_service._detect_chunk_type("他走进了那座古老的城堡。")
        assert chunk_type == "scene"


class TestSplitIntoParagraphs:
    """测试 _split_into_paragraphs 方法"""

    def test_basic_splitting(self, chunking_service: ChunkingService):
        """基本段落分割：长段落保持独立"""
        # 每个段落超过 50 字，不会被合并
        content = "这是一个足够长的段落，超过五十个字的限制，不会被与其他段落合并处理，确保长度足够。" * 3
        content = content + "\n\n" + content + "\n\n" + content
        paragraphs = chunking_service._split_into_paragraphs(content)
        assert len(paragraphs) == 3

    def test_empty_lines_filtered(self, chunking_service: ChunkingService):
        """空行被过滤"""
        content = "这是一个足够长的段落，超过五十个字的限制，确保长度足够长不会被合并处理。" * 3
        content = content + "\n\n\n\n" + content
        paragraphs = chunking_service._split_into_paragraphs(content)
        assert len(paragraphs) == 2

    def test_whitespace_stripped(self, chunking_service: ChunkingService):
        """首尾空白被去除"""
        content = "这是一个足够长的段落，超过五十个字的限制，不会被合并处理，确保长度足够。" * 3
        content = "  " + content + "  \n\n  " + content + "  "
        paragraphs = chunking_service._split_into_paragraphs(content)
        assert paragraphs[0].startswith("这是")
        assert paragraphs[1].startswith("这是")

    def test_newline_normalization(self, chunking_service: ChunkingService):
        """换行符统一化"""
        content = "这是一个足够长的段落，超过五十个字的限制，确保长度足够长不会被合并处理。" * 3
        content = content + "\r\n" + content + "\r" + content
        paragraphs = chunking_service._split_into_paragraphs(content)
        assert len(paragraphs) == 3

    def test_short_paragraph_merging(self, chunking_service: ChunkingService):
        """短段落合并（< 50 字）：短段落持续合并直到达到阈值"""
        # 短段落会被合并在一起，直到总长度 >= 50
        long_text = "这是一个足够长的段落，超过五十个字的限制，不会被合并处理，确保长度足够。" * 3
        content = "短一\n短二\n" + long_text
        paragraphs = chunking_service._split_into_paragraphs(content)
        # "短一"、"短二" 合并后仍不足 50 字，会继续与长段落合并成一个
        # 长段落被合并后超过 50 字，整个成为一段
        assert len(paragraphs) == 1
        assert "短一" in paragraphs[0]
        assert "短二" in paragraphs[0]

    def test_short_then_long_split(self, chunking_service: ChunkingService):
        """短段落合并后达到阈值则分开"""
        # 短段落合并后 >= 50 字时，下一段独立
        long_text = "这是一个足够长的段落，超过五十个字的限制，不会被合并处理，确保长度足够。" * 3
        # 用一个 50+ 字的段落作为第一个，短段落作为第二个
        content = long_text + "\n" + "短段落" + "\n" + long_text
        paragraphs = chunking_service._split_into_paragraphs(content)
        # 第一个长段落独立，短段落与最后一个长段落合并
        assert len(paragraphs) == 2

    def test_empty_content(self, chunking_service: ChunkingService):
        """空内容返回空列表"""
        paragraphs = chunking_service._split_into_paragraphs("")
        assert paragraphs == []
