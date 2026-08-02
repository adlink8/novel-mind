"""Skill Runtime 服务层（25.2-03）。

技能注册、确定性 finalizer、产物状态迁移。所有持久化写路径都经由
本包中的 service 函数——agent loop 不直接触碰 artifact 表（D-01/D-11）。
"""
