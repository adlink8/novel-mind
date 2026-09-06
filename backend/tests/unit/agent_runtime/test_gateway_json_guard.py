"""网关 JSON 护栏条件化的单元回归（E2E run 116/118）。

response_format=json_object 在 OpenAI 兼容 API 下会抑制原生 tool_calls：
wmc 曾因此三轮把工具调用写成文本 JSON、内核零执行。带 tools 的请求
必须放行原生工具调用，护栏只对无 tools 的最终输出轮生效。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from app.api.gateway import _json_response_format


def test_json_guard_suppressed_when_tools_present():
    """带 tools 的轮次绝不强制 json_object（否则原生工具调用被抑制）。"""
    assert _json_response_format("propose-world-model-candidates", has_tools=True) is None
    assert _json_response_format("build-visual-bible", has_tools=True) is None


def test_json_guard_applies_without_tools():
    """无 tools 的请求保留 JSON 护栏（fail-closed 前的确定性护栏）。"""
    assert _json_response_format("propose-world-model-candidates") == {
        "type": "json_object"
    }


def test_json_guard_untouched_for_prose_skills():
    """散文类技能（问答）不受护栏影响。"""
    assert _json_response_format("answer-reading-question") is None
    assert _json_response_format("answer-reading-question", has_tools=True) is None
