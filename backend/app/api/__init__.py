"""
API 路由层

本包包含 6 个路由模块，每个模块对应一个业务域:
  - novels.py     : 小说管理（上传、列表、详情、删除、章节查询）
  - analysis.py   : 剧情分析（全书分析、章节分析、流式分析）→ 占位，返回 501
  - timeline.py   : 时间线（查询、提取、编辑、删除事件）→ 占位，返回 501
  - characters.py : 人物关系（人物列表、关系网络、AI 抽取）→ 占位，返回 501
  - fanfiction.py : 同人文（列表、创建、AI 续写）→ 占位，返回 501
  - models.py     : AI 模型配置（CRUD、测试连接、设为默认）

在 main.py 中注册:
  app.include_router(novels.router, prefix="/api/novels", tags=["小说管理"])
"""
