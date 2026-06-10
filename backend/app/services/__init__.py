"""
业务逻辑服务层

本包包含 NovelMind 的核心业务逻辑:
  - novel_service.py : 小说处理服务（文件上传、编码检测、章节分割、CRUD）
  - ai_service.py    : AI 调用服务（LiteLLM 统一封装：chat、embedding、stream_chat）
  - ai_router.py     : AI 智能路由器（按任务类型和层级选择最优模型）

调用链:
  API 路由 → Service 层 → 数据库/AI 服务
  API 路由 → ai_router.route_task() → ai_service.chat() → LiteLLM → 外部 API
"""
