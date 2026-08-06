"""hunyuan_transport 单测：图片探测 + 响应解析 + 异常分类。"""

from __future__ import annotations

import base64

import pytest

from app.services.illustrations.hunyuan_transport import (
    HunyuanIllustrationTransport,
    HunyuanRejectedError,
    _extract_image,
    _probe_image,
)

pytestmark = pytest.mark.unit

# 1x1 PNG（IHDR 宽高 = 1x1）
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x00" * 8


class TestProbeImage:
    def test_png_detects_mime_and_size(self):
        mime, size = _probe_image(_PNG)
        assert mime == "image/png"
        assert size == (1, 1)

    def test_jpeg_detects_mime(self):
        mime, size = _probe_image(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
        assert mime == "image/jpeg"
        assert size == (1024, 1024)  # 无 SOF 时回退默认

    def test_unknown_fallback(self):
        mime, size = _probe_image(b"garbage-data")
        assert mime == "image/jpeg"
        assert size == (1024, 1024)


class TestExtractImage:
    def test_b64_json(self):
        payload = _PNG
        data = {"data": [{"b64_json": base64.b64encode(payload).decode("ascii")}]}
        assert _extract_image(data, base_url="http://x") == payload

    def test_data_url(self):
        payload = _PNG
        url = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
        data = {"data": [{"url": url}]}
        assert _extract_image(data, base_url="http://x") == payload

    def test_no_image_returns_none(self):
        assert _extract_image({"data": []}, base_url="http://x") is None


class TestTransportErrors:
    def test_rejected_error_has_provider_marker(self):
        assert HunyuanRejectedError("x").provider_rejected is True

    def test_generate_rejects_empty_prompt(self):
        import asyncio

        t = HunyuanIllustrationTransport()
        with pytest.raises(HunyuanRejectedError):
            asyncio.run(t.generate(prompt="  ", width=1024, height=1024))
