"""腾讯混元 hunyuan-image 真实生图 transport（ZCodeProxy 本地代理）。

实现 ``IllustrationTransport``：走本地 ZCodeProxy 的 OpenAI 兼容
``/v1/images/generations`` 接口生成真实图片（Phase 33-02 provider seam）。

连接信息（Git 历史 feat/phase21-debtfix 先例 + 本机实测）：
- base_url: ``http://127.0.0.1:3001``（ZCodeProxy 本地代理）
- 认证：无（本地代理不鉴权）
- 请求：``{"model":"hunyuan-image","prompt":...,"size":"1024x1024","response_format":"b64_json"}``
- 响应：``data[0].b64_json``（base64 JPEG/PNG）；兼容 ``data[0].url``

设计要点：
- httpx 直连（零新依赖）；proxy 可复用 config.https_proxy；
- 探测 jpg/png 魔数定 mime_type 与宽高（JPEG 解析 SOF 段得宽高，PNG 用 IHDR）；
- usage：OpenAI 兼容响应无 token 统计，输入 token 按 prompt 字符数估算；
- 异常分类：4xx（非 429）→ ``HunyuanRejectedError``（``provider_rejected``
  marker，gateway 识别为 ProviderRejected）；429/5xx/超时 → 普通异常
  （gateway 走 ProviderOutcomeUnknown）。

安全：响应体/错误信息经 gateway.redact_provider_error 清洗，无密钥。
"""

from __future__ import annotations

import base64
import hashlib
import struct
from typing import Any

import httpx

from app.services.illustrations.gateway import ProviderResponse


class HunyuanTransportError(RuntimeError):
    """transport 失败基类；gateway 已把所有异常转 outcome_unknown。"""


class HunyuanRejectedError(HunyuanTransportError):
    """已知坏请求（4xx 非 429）：应为 ProviderRejected 语义（provider 拒）。"""

    provider_rejected = True


def _probe_image(payload: bytes) -> tuple[str, tuple[int, int]]:
    """探测图片 mime_type 与宽高；未知格式回退 image/jpeg + 1024x1024。"""
    mime = "image/jpeg"
    size = (1024, 1024)
    if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
        mime = "image/png"
        try:
            w, h = struct.unpack(">II", payload[16:24])
            if w > 0 and h > 0:
                size = (w, h)
        except struct.error:
            pass
    elif payload.startswith(b"\xff\xd8") and len(payload) >= 4:
        mime = "image/jpeg"
        parsed = _jpeg_size(payload)
        if parsed:
            size = parsed
    elif payload.startswith(b"GIF8"):
        mime = "image/gif"
    return mime, size


def _jpeg_size(payload: bytes) -> tuple[int, int] | None:
    """解析 JPEG SOF 段得宽高；未找到返回 None。"""
    i = 2
    n = len(payload)
    while i + 9 < n:
        if payload[i] != 0xFF:
            i += 1
            continue
        marker = payload[i + 1]
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", payload[i + 2 : i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            try:
                height = struct.unpack(">H", payload[i + 5 : i + 7])[0]
                width = struct.unpack(">H", payload[i + 7 : i + 9])[0]
            except struct.error:
                return None
            if width > 0 and height > 0:
                return width, height
        i += 2 + length
    return None


class HunyuanIllustrationTransport:
    """真实混元生图 transport（ZCodeProxy OpenAI 兼容 images API）。"""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:3001",
        model: str = "hunyuan-image",
        proxy: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.proxy = proxy or None
        self.timeout = timeout

    async def generate(self, **kwargs: Any) -> ProviderResponse:
        prompt = str(kwargs.get("prompt") or "")
        if not prompt.strip():
            raise HunyuanRejectedError("hunyuan provider: empty prompt rejected")
        width = int(kwargs.get("width") or 1024)
        height = int(kwargs.get("height") or 1024)
        timeout = float(kwargs.get("timeout") or self.timeout)

        body = {
            "model": self.model,
            "prompt": prompt,
            "size": f"{width}x{height}",
            "response_format": "b64_json",
        }
        url = f"{self.base_url}/v1/images/generations"
        try:
            async with httpx.AsyncClient(timeout=timeout, proxy=self.proxy) as client:
                resp = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            # 连接错误/超时：provider outcome unknown（可重试可对账）。
            raise RuntimeError(
                f"hunyuan provider transport error "
                f"(确认 ZCodeProxy 已启动 {self.base_url}): {exc}"
            ) from exc

        if resp.status_code in (429, 500, 502, 503, 504):
            raise RuntimeError(f"hunyuan provider returned HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise HunyuanRejectedError(
                f"hunyuan provider rejected request: HTTP {resp.status_code}"
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError("hunyuan provider returned non-JSON body") from exc

        payload_bytes = _extract_image(data, base_url=self.base_url)
        if not payload_bytes:
            raise RuntimeError("hunyuan provider returned no image payload")

        mime_type, actual = _probe_image(payload_bytes)
        digest = hashlib.sha256(payload_bytes).hexdigest()
        # OpenAI 兼容响应无 token 统计：按 prompt 字符数估算输入 token。
        usage = {
            "input_tokens": max(1, len(prompt) // 2),
            "output_tokens": 0,
        }
        return ProviderResponse(
            payload=payload_bytes,
            mime_type=mime_type,
            width=actual[0],
            height=actual[1],
            provider="hunyuan",
            provider_model=self.model,
            provider_request_id=f"hunyuan-{digest[:16]}",
            usage=usage,
            response_metadata={"provider": "hunyuan", "source": "zcodeproxy"},
        )


def _extract_image(data: dict[str, Any], *, base_url: str) -> bytes | None:
    """从 OpenAI 兼容 images API 响应提取图片字节。

    优先 ``data[0].b64_json``（base64 解码）；兼容 ``data[0].url``
    （data: URL 直接解，http(s) 下载）。
    """
    items = data.get("data") or []
    if not items:
        return None
    item = items[0]
    b64 = item.get("b64_json")
    if b64:
        try:
            return base64.b64decode(b64)
        except (ValueError, TypeError):
            return None
    url = item.get("url")
    if url:
        return _fetch_url(url, base_url=base_url)
    return None


def _fetch_url(url: str, *, base_url: str) -> bytes | None:
    """下载 url 指向的图片字节；data: URL 直接解；同步 helper（异步用 run）。"""
    try:
        if url.startswith("data:"):
            _, payload = url.split(",", 1)
            return base64.b64decode(payload)
        if url.startswith(("http://", "https://")):
            if url.startswith("/"):
                url = base_url.rstrip("/") + url
            return _download_sync(url)
    except (ValueError, TypeError):
        return None
    return None


def _download_sync(url: str) -> bytes | None:
    """同步下载（transport 内已处 async 上下文，用 run 桥接）。"""
    import asyncio

    try:
        return asyncio.run(_download(url))
    except (RuntimeError, Exception):  # noqa: BLE001
        return None


async def _download(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
    except httpx.HTTPError:
        return None
    return None
