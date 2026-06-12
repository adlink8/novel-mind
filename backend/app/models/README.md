# backend/app/models — ORM 模型层

SQLAlchemy 2.0 异步声明式模型，映射到 PostgreSQL 16 数据库。所有模型继承自 `Base`（`base.py`）。

## 模型清单（13 张表）

| 模型 | 文件 | 所属域 | 说明 |
|---|---|---|---|
| `Base` | `base.py` | 基础设施 | SQLAlchemy 声明式基类，所有模型的父类 |
| `User` | `user.py` | 认证 | 用户主体 — username / email / password_hash，关联 novels / ai_model_configs |
| `Novel` | `novel.py` | 小说 | 小说主表 — 标题 / 作者 / 字数 / 状态 / 文风指纹，关联 chapters / characters / analyses |
| `Chapter` | `novel.py` | 小说 | 章节表 — 标题 / 序号 / 内容 / 字数 / 词频，外键 → novels |
| `ImportJob` | `import_job.py` | 导入 | 导入任务状态机 — pending → processing → completed / failed，支持重试 |
| `TextChunk` | `text_chunk.py` | RAG | 文本块 — 内容 / 块类型 / 章节引用 / embedding 元数据 |
| `AnalysisResult` | `analysis.py` | 分析 | AI 分析结果 — 摘要 / 人物分析 / 叙事结构，归属 novel / chapter |
| `Character` | `character.py` | 人物 | 人物表 — 名称 / 别名 / 角色类型 / 性格描述 |
| `CharacterRelation` | `character.py` | 人物 | 人物关系 — source / target / 关系类型 / 强度 |
| `TimelineEvent` | `timeline.py` | 时间线 | 时间线事件 — 标题 / 类型 / 因果关联 |
| `FanFiction` | `fanfiction.py` | 同人 | 同人文 — 续写提示 / 风格配置 / 生成状态 |
| `FanFictionChapter` | `fanfiction_chapter.py` | 同人 | 同人文章节 — AI 生成标记 / 风格评分 / RAG 上下文 |
| `AIModelConfig` | `ai_model.py` | AI 配置 | AI 模型配置 — 提供商 / 加密密钥 / 路由层级 / owner 隔离 |
| `AIUsageLog` | `ai_usage_log.py` | AI 配置 | AI 调用日志 — token 用量 / 费用 / 延迟 |

## 数据库迁移

通过 Alembic 管理，当前 head: `f3c8b7b2dbf7`。

```bash
cd backend
alembic upgrade head    # 应用迁移
alembic current         # 查看当前版本
alembic revision --autogenerate -m "描述"  # 生成新迁移
```

## 约定

- 使用 `Mapped[str]` 类型注解，非 legacy `Column()`
- 外键使用 `ForeignKey("table.column", ondelete="CASCADE")`
- 时间戳字段统一用 `func.now()` 默认值
- 新增模型后必须生成 Alembic 迁移并 `upgrade head`
