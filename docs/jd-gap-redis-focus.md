# feat/jd-redis-focus

焦点科技「Java开发工程师（AI应用部）」JD 要求缓存（Redis）。本分支在 NovelMind RAG 检索链路上增加检索结果缓存：

- 代码：`backend/app/services/retrieval_cache.py`
- 接线：`HybridSearchService._vector_search` 命中缓存则跳过 embedding + Chroma
- 默认内存后端（单测/无 Redis 时）；`NOVELMIND_REDIS_URL` 指向真实 Redis
- 演示：`cd backend && python -m pytest tests/test_retrieval_cache.py -q`

**不合并 master。** 面试现场 `git checkout feat/jd-redis-focus`。
