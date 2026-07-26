"""
API 路由层

路由模块与真实状态（2026-07-26 对齐，完整注册见 main.py）:
  - novels.py     : 小说管理 — 已实现
  - analysis.py   : 版本化剧情分析（Phase 08）— 已实现；仅 analyze/stream 仍 501
  - timeline.py   : 时间线（Phase 08）— 已实现
  - characters.py : 旧占位（空数组/501）— 废弃双轨，Phase 25 处置；新系统见 relationships
  - fanfiction.py : 同人文占位（501）— deferred，v1.4 创作域接管
  - models.py     : AI 模型配置 — 已实现
  - settings.py / usage.py : AI 路由偏好与用量 — 已实现
  另有 clues、relationships、reader chat、narrative memory、knowledge、eval、
  chunking、asset audit 等模块，均在 main.py 注册。

在 main.py 中注册:
  app.include_router(novels.router, prefix="/api/novels", tags=["小说管理"])
"""
