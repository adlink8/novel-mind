"""Live chunker qualification placeholder (not in default PR)."""
from __future__ import annotations
import pytest

pytestmark = [pytest.mark.live, pytest.mark.integration]

@pytest.mark.skip(reason="live/nightly only — requires local model provider")
def test_live_chunker_qualification_placeholder():
    assert True
