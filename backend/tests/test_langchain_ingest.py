"""LangChain 切分适配器单测：必须真切出 Document，禁止空壳 import。"""

from __future__ import annotations

import pytest

from app.services.langchain_ingest import documents_to_chunks, split_to_documents

pytestmark = pytest.mark.unit


def test_split_creates_langchain_documents():
    text = "龙影出鞘。" * 80
    docs = split_to_documents(text, chapter_id=12, chunk_size=120, chunk_overlap=20)
    assert len(docs) >= 2
    assert all(d.metadata["chapter_id"] == 12 for d in docs)
    assert all(d.page_content for d in docs)
    assert docs[0].metadata.get("source") == "langchain_ingest"


def test_chunks_preserve_overlap_and_order():
    text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 10
    docs = split_to_documents(text, chapter_id=1, chunk_size=40, chunk_overlap=10)
    chunks = documents_to_chunks(docs)
    assert chunks[0]["index"] == 0
    assert "start_index" in chunks[0]["metadata"] or chunks[0]["start_index"] is not None
    contents = [c["content"] for c in chunks]
    assert contents[0][:10] == text[:10]
