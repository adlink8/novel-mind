"""agent-tools 422 校验错误的字段级明细浮现（E2E 底座修复）。

FastAPI 原生 RequestValidationError 只携带 errors() 结构；agent-tools 路径
此前返回泛化的「参数校验失败」，模型拿不到哪个字段错，只能盲目重试
（E2E 实测 get_evidence_span 连续 422 直至烧穿 max_calls 熔断）。
handler 必须把 loc/msg 明细放进 invalid_input 信封的 message。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.exceptions import RequestValidationError

from app.main import request_validation_error_handler

pytestmark = pytest.mark.unit


def _request(path: str):
    return SimpleNamespace(url=SimpleNamespace(path=path))


async def test_agent_tools_422_message_carries_field_details():
    exc = RequestValidationError(
        errors=[
            {
                "type": "missing",
                "loc": ("body", "source_start"),
                "msg": "Field required",
            }
        ]
    )

    response = await request_validation_error_handler(
        _request("/api/agent-tools/get_evidence_span"), exc
    )

    assert response.status_code == 422
    payload = json.loads(bytes(response.body))
    assert payload["error"]["code"] == "invalid_input"
    assert "source_start" in payload["error"]["message"]


async def test_non_agent_paths_keep_default_detail_shape():
    exc = RequestValidationError(
        errors=[{"type": "missing", "loc": ("body", "x"), "msg": "Field required"}]
    )

    response = await request_validation_error_handler(_request("/api/novels"), exc)

    assert response.status_code == 422
    payload = json.loads(bytes(response.body))
    assert "detail" in payload
