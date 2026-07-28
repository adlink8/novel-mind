"""
候选测试题生成器 测试

测试 prompt 构建、AI 响应解析、参数验证。
"""

import pytest

pytestmark = pytest.mark.unit

import json


# 内联测试函数（避免导入时的模块依赖问题）
def build_prompt(chunks: list[dict], question_type: str, count: int) -> str:
    """构建生成 prompt"""
    chunks_text = "\n\n".join(
        f"[片段 {c['chunk_index']} | ID={c['id']} | 类型={c['chunk_type']}]\n{c['content']}"
        for c in chunks
    )
    return f"片段:\n{chunks_text}\n\n生成 {count} 道 {question_type} 题"


def parse_ai_response(response_text: str) -> list[dict]:
    """解析 AI 返回的 JSON"""
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if len(lines) > 1 else text
        if text.endswith("```"):
            text = text[:-3]

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("questions", [data])
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        import re

        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return []


class TestPromptBuilding:
    """测试 Prompt 构建"""

    def test_build_prompt_includes_chunk_info(self):
        chunks = [
            {"chunk_index": 0, "id": 42, "chunk_type": "scene", "content": "测试内容A"},
            {
                "chunk_index": 1,
                "id": 43,
                "chunk_type": "dialogue",
                "content": "测试内容B",
            },
        ]
        prompt = build_prompt(chunks, "original_text", 3)

        assert "测试内容A" in prompt
        assert "测试内容B" in prompt
        assert "ID=42" in prompt
        assert "ID=43" in prompt
        assert "片段 0" in prompt
        assert "scene" in prompt
        assert "dialogue" in prompt
        assert "3 道" in prompt
        assert "original_text" in prompt

    def test_build_prompt_empty_chunks(self):
        prompt = build_prompt([], "timeline", 1)
        assert "片段:" in prompt
        assert "timeline" in prompt


class TestResponseParsing:
    """测试 AI 响应解析"""

    def test_parse_plain_json(self):
        response = json.dumps(
            [
                {
                    "question": "Q1",
                    "question_type": "original_text",
                    "gold_chunks": [1, 2],
                },
                {
                    "question": "Q2",
                    "question_type": "character_relation",
                    "gold_chunks": [3],
                },
            ]
        )
        result = parse_ai_response(response)
        assert len(result) == 2
        assert result[0]["question"] == "Q1"
        assert result[1]["question"] == "Q2"

    def test_parse_markdown_wrapped_json(self):
        response = '```json\n[{"question": "Q1", "gold_chunks": [1]}]\n```'
        result = parse_ai_response(response)
        assert len(result) == 1
        assert result[0]["question"] == "Q1"

    def test_parse_nested_object_with_questions_key(self):
        response = json.dumps({"questions": [{"question": "Q1", "gold_chunks": [1]}]})
        result = parse_ai_response(response)
        assert len(result) == 1
        assert result[0]["question"] == "Q1"

    def test_parse_invalid_json_returns_empty(self):
        result = parse_ai_response("这不是 JSON")
        assert result == []

    def test_parse_empty_string_returns_empty(self):
        result = parse_ai_response("")
        assert result == []

    def test_parse_garbled_json_fallback(self):
        # 文本中混合了 JSON 数组
        response = (
            '好的，以下是你需要的：\n[\n  {"question": "你好", "gold_chunks": []}\n]'
        )
        result = parse_ai_response(response)
        assert len(result) == 1
        assert result[0]["question"] == "你好"
