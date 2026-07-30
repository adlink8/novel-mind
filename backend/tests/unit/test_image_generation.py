import pytest

from app.services.image_generation import _image_dimensions, _prompt_parts
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


def test_png_dimensions_are_read_without_pillow():
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1024).to_bytes(4, "big") + (768).to_bytes(4, "big")
    assert _image_dimensions(png_header) == (1024, 768)
