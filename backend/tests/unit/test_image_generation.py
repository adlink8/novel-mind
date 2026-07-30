import base64
from types import SimpleNamespace

import httpx
import pytest

from app.services import image_generation
from app.services.image_generation import (
    _build_enrichment_messages,
    _enrich_prompt,
    _image_dimensions,
    _prompt_parts,
    _provider_error_detail,
)
from app.schemas.reader_chat import ImageGenerationRequest


pytestmark = pytest.mark.unit


def test_image_prompt_combines_selection_and_refinement():
    prompt_cn, prompt = _prompt_parts(
        ImageGenerationRequest(selected_text="林间的雨", user_refine="电影感、广角")
    )

    assert prompt_cn == "林间的雨\n\n画面补充：电影感、广角"
    assert prompt == prompt_cn


def test_image_prompt_rejects_empty_input():
    with pytest.raises(Exception, match="选择文本或输入画面描述"):
        _prompt_parts(ImageGenerationRequest())


def test_enrichment_messages_include_novel_character_and_history_context():
    messages = _build_enrichment_messages(
        base_prompt="魔王城决战",
        context={
            "novel_title": "转生史莱姆",
            "genre": "东方玄幻",
            "world_description": "魔法与魔王共存的世界",
            "style_fingerprint": "浓郁色彩",
            "chapter_title": "魔王城决战",
            "characters": ["利姆路（主角）；外观/身份：蓝色长发"],
            "history_prompts": ["日系轻小说插画，冷色魔法光效"],
        },
    )
    assert "转生史莱姆" in messages[1]["content"]
    assert "蓝色长发" in messages[1]["content"]
    assert "日系轻小说插画" in messages[1]["content"]


@pytest.mark.asyncio
async def test_enrich_prompt_uses_llm_result(monkeypatch):
    async def fake_chat(**_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="东方玄幻，蓝色长发主角在魔王城决战")
                )
            ]
        )

    monkeypatch.setattr(image_generation.ai_service, "chat", fake_chat)
    async def fake_context(*_args, **_kwargs):
        return {
            "novel_title": "转生史莱姆",
            "characters": [],
            "history_prompts": [],
        }

    monkeypatch.setattr(image_generation, "_load_prompt_context", fake_context)

    result = await _enrich_prompt(
        None,
        novel=SimpleNamespace(title="转生史莱姆", style_fingerprint=None),
        owner_id=1,
        chapter=None,
        base_prompt="魔王城决战",
    )

    assert "蓝色长发" in result


@pytest.mark.asyncio
async def test_enrich_prompt_falls_back_when_llm_fails(monkeypatch):
    async def failing_chat(**_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(image_generation.ai_service, "chat", failing_chat)
    async def fake_context(*_args, **_kwargs):
        return {"novel_title": "小说"}

    monkeypatch.setattr(image_generation, "_load_prompt_context", fake_context)

    assert (
        await _enrich_prompt(
            None,
            novel=SimpleNamespace(title="小说", style_fingerprint=None),
            owner_id=1,
            chapter=None,
            base_prompt="一座城堡",
        )
        == "一座城堡"
    )


def test_png_dimensions_are_read_without_pillow():
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1024).to_bytes(4, "big") + (768).to_bytes(4, "big")
    assert _image_dimensions(png_header) == (1024, 768)


def test_provider_error_detail_reads_openai_style_error():
    response = httpx.Response(
        500,
        json={"error": {"message": "CloudBase quota exceeded"}},
    )

    assert _provider_error_detail(response) == "CloudBase quota exceeded"


def test_provider_error_detail_returns_none_for_non_json_response():
    response = httpx.Response(502, content=b"upstream unavailable")

    assert _provider_error_detail(response) is None


@pytest.mark.asyncio
async def test_request_image_accepts_proxy_b64_response(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    request_payload = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _endpoint, *, json):
            request_payload.update(json)
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(png).decode()}]},
                request=httpx.Request("POST", "http://127.0.0.1:3001/v1/images/generations"),
            )

    monkeypatch.setattr(image_generation.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    image_bytes, extension = await image_generation._request_image("一座城堡")

    assert image_bytes == png
    assert extension == "png"
    assert request_payload["response_format"] == "b64_json"
