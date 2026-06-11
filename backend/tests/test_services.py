"""
NovelService 单元测试

覆盖范围:
- 文本清洗（BOM 去除、换行统一、空行合并）
- 章节分割（中文章节、英文 Chapter、数字标题、无章节标记）
- 编码检测与回退（GBK → UTF-8）
- 字数统计

注意:
- 不依赖数据库，纯单元测试
- 直接实例化 NovelService 测试解析逻辑
"""

import io

import pytest
from fastapi import UploadFile

from app.services.novel_service import novel_service


class TestParseNovel:
    """测试 parse_novel 的文本解析与章节分割"""

    def test_parse_chinese_chapters(self):
        """中文章节标题分割"""
        content = (
            "第一章 初入江湖\n\n这是第一章内容。\n\n第二章 拜师学艺\n\n这是第二章内容。"
        )
        chapters = novel_service.parse_novel(content)
        assert len(chapters) == 2
        assert chapters[0]["chapter_number"] == 1
        assert chapters[0]["title"] == "第一章 初入江湖"
        assert "初入江湖" in chapters[0]["content"]
        assert chapters[1]["chapter_number"] == 2
        assert chapters[1]["title"] == "第二章 拜师学艺"

    def test_parse_english_chapters(self):
        """英文 Chapter 标题分割"""
        content = (
            "Chapter 1 The Beginning\n\n"
            "Content of chapter 1.\n\n"
            "Chapter 2 The Journey\n\n"
            "Content of chapter 2."
        )
        chapters = novel_service.parse_novel(content)
        assert len(chapters) == 2
        assert chapters[0]["title"] == "Chapter 1 The Beginning"
        assert chapters[1]["title"] == "Chapter 2 The Journey"

    def test_parse_numbered_chapters(self):
        """数字标题分割（"1. 标题" 格式）"""
        content = "1. 引言\n\n引言内容。\n\n2. 正文\n\n正文内容。"
        chapters = novel_service.parse_novel(content)
        assert len(chapters) == 2
        assert chapters[0]["title"] == "1. 引言"
        assert chapters[1]["title"] == "2. 正文"

    def test_parse_no_chapters(self):
        """无章节标记时作为单章处理"""
        content = "这是一段没有任何章节标记的纯文本内容。"
        chapters = novel_service.parse_novel(content)
        assert len(chapters) == 1
        assert chapters[0]["chapter_number"] == 1
        assert chapters[0]["title"] == "全文"

    def test_parse_with_preamble(self):
        """前言超过 100 字时单独成章"""
        preamble = "前言测试内容 " * 20  # 超过 100 字符
        content = f"{preamble}\n\n第一章 开始\n\n正文内容。"
        chapters = novel_service.parse_novel(content)
        assert len(chapters) == 2
        assert chapters[0]["chapter_number"] == 0
        assert chapters[0]["title"] == "前言"
        assert chapters[1]["chapter_number"] == 1
        assert chapters[1]["title"] == "第一章 开始"

    def test_parse_word_count(self):
        """字数统计正确"""
        content = "第一章\n\n" + "内容" * 100
        chapters = novel_service.parse_novel(content)
        assert chapters[0]["word_count"] == len(chapters[0]["content"])
        assert chapters[0]["word_count"] > 0

    def test_parse_bom_removal(self):
        """去除 UTF-8 BOM 标记"""
        content = "\ufeff第一章\n\n内容。"
        chapters = novel_service.parse_novel(content)
        assert chapters[0]["title"] == "第一章"
        assert "\ufeff" not in chapters[0]["content"]

    def test_parse_newline_normalization(self):
        """统一换行符（\\r\\n / \\r → \\n）"""
        content = "第一章\r\n\r\n内容\r结束。"
        chapters = novel_service.parse_novel(content)
        assert "\r" not in chapters[0]["content"]

    def test_parse_empty_line_merge(self):
        """合并连续空行"""
        content = "第一章\n\n\n\n\n内容。"
        chapters = novel_service.parse_novel(content)
        # 多个空行应被合并为两个
        assert "\n\n\n\n" not in chapters[0]["content"]


class TestUploadNovel:
    """测试 upload_novel 的文件处理"""

    @pytest.mark.asyncio
    async def test_upload_utf8(self):
        """UTF-8 编码文件正常读取（验证随机文件名安全存储）"""
        content = "测试内容"
        file = UploadFile(filename="test.txt", file=io.BytesIO(content.encode("utf-8")))
        path, text = await novel_service.upload_novel(file)
        assert text == content
        # 使用随机文件名，不再以原始文件名结尾
        assert path.endswith(".txt")
        assert "uploads" in path

    @pytest.mark.asyncio
    async def test_upload_gbk(self):
        """GBK 编码文件自动检测并转换"""
        content = "中文测试内容"
        file = UploadFile(filename="gbk.txt", file=io.BytesIO(content.encode("gbk")))
        path, text = await novel_service.upload_novel(file)
        assert text == content
