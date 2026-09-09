"""LangChain 文档切分适配器。

对照焦点 JD「LangChain 等 AI 开发框架优先」：用官方 RecursiveCharacterTextSplitter
把章节切成 LangChain Document，再交给现有 Chroma 索引。不替换原生 RAG。
"""

from __future__ import annotations

from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


def split_to_documents(
    text: str,
    *,
    chapter_id: int,
    chunk_size: int = 400,
    chunk_overlap: int = 50,
) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        add_start_index=True,
    )
    return splitter.create_documents(
        [text],
        metadatas=[{"chapter_id": chapter_id, "source": "langchain_ingest"}],
    )


def documents_to_chunks(docs: list[Document]) -> list[dict[str, Any]]:
    chunks = []
    for i, doc in enumerate(docs):
        chunks.append(
            {
                "index": i,
                "content": doc.page_content,
                "metadata": dict(doc.metadata),
                "start_index": doc.metadata.get("start_index"),
            }
        )
    return chunks
