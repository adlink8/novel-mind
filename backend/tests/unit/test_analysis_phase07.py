"""Phase 07 structural analysis unit tests (no LLM / no DB hierarchy required)."""

from __future__ import annotations

import pytest

from app.models.novel import Novel
from app.services.analysis_service import SUPPORTED_TYPES, build_structural_result

pytestmark = pytest.mark.unit


def test_supported_types_include_hierarchy_map():
    assert "hierarchy_map" in SUPPORTED_TYPES
    assert "plot_summary" in SUPPORTED_TYPES


def test_plot_summary_from_scenes():
    novel = Novel(title="测试书", owner_id=1)
    scenes = [
        {
            "scene_id": f"s{i}",
            "chapter_id": 1 + i // 3,
            "chapter_number": 1 + i // 3,
            "order_index": i,
            "char_count": 100 + i,
            "evidence_count": 2,
            "preview": f"场景预览{i} " * 5,
        }
        for i in range(12)
    ]
    data = build_structural_result(
        novel=novel,
        analysis_type="plot_summary",
        scenes=scenes,
        chapter_id=None,
        build_id="cb_test",
    )
    assert data["source"] == "phase07_hierarchy"
    assert data["scene_count"] == 12
    assert data["beats"]
    assert data["llm_enriched"] is False


def test_hierarchy_map_lists_scenes():
    novel = Novel(title="地图", owner_id=1)
    scenes = [
        {
            "scene_id": "s0",
            "chapter_id": 1,
            "chapter_number": 1,
            "order_index": 0,
            "char_count": 50,
            "evidence_count": 1,
            "preview": "开场",
        }
    ]
    data = build_structural_result(
        novel=novel,
        analysis_type="hierarchy_map",
        scenes=scenes,
        chapter_id=None,
        build_id="cb_x",
    )
    assert data["scenes"][0]["scene_id"] == "s0"
