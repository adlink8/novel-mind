"""
业务逻辑服务层

本包包含 NovelMind 的核心业务逻辑:
  - novel_service.py  : 小说处理服务（文件上传、编码检测、章节分割、CRUD）
  - import_service.py : 导入任务服务（状态机、进度跟踪、重试逻辑）
  - ai_service.py     : AI 调用服务（LiteLLM 统一封装：chat、embedding、stream_chat）
  - ai_router.py      : AI 智能路由器（按任务类型和层级选择最优模型）
  - chunking_service.py: 文本分块服务（语义分块、块类型检测）
  - vector_store.py   : 向量存储服务（ChromaDB 封装：写入、搜索、删除）

调用链:
  API 路由 → Service 层 → 数据库/AI 服务
  API 路由 → ai_router.route_task() → ai_service.chat() → LiteLLM → 外部 API
  RAG 检索 → vector_store.search() → ChromaDB → 语义结果
"""
