# feat/jd-langchain-focus

对照焦点 JD「熟悉 LangChain 等 AI 开发框架者优先」。

- 代码：`backend/app/services/langchain_ingest.py`
- 用官方 `RecursiveCharacterTextSplitter` 把章节切成 `langchain_core.documents.Document`
- 不替换原生层级 RAG，只作为入库切分适配器
- 演示：`python -m pytest tests/test_langchain_ingest.py -q`

对照 GitHub 同类：`langchain-chromadb-rag-example` 一类最小 RAG。本分支只保留切分这一层，避免把未跑通的 LLM chain 写进简历。
