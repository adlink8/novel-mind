# backend/migrations — 数据库迁移

Alembic 管理的 PostgreSQL 数据库版本迁移。确保 ORM 模型层（`app/models/`）与数据库 schema 保持同步。

## 结构

```
migrations/
├── env.py               # Alembic 环境配置 — 异步引擎、元数据绑定
├── script.py.mako       # 迁移脚本模板
├── versions/            # 迁移版本文件
│   └── f3c8b7b2dbf7_*.py   # 当前 head
└── README.md
```

## 常用命令

```bash
cd backend
source venv/Scripts/activate

# 查看当前数据库版本
alembic current

# 查看迁移历史
alembic history

# 应用所有未执行迁移
alembic upgrade head

# 生成自动迁移（ORM 模型变更后）
alembic revision --autogenerate -m "描述变更内容"

# 回滚一个版本
alembic downgrade -1
```

## 注意事项

1. **Alembic 编码补丁**：Windows GBK 环境下，需 patch `alembic/util/compat.py`，将 `encoding="locale"` 改为 `encoding="utf-8"`
2. **每次新增/修改 ORM 模型后**，必须生成迁移并 `upgrade head`，否则 500 错误
3. **验证一致性**：`alembic current` 应该等于 `alembic heads`

## 当前状态

- **Head**: `f3c8b7b2dbf7`
- **表数量**: 13 张（User, Novel, Chapter, ImportJob, TextChunk, AnalysisResult, Character, CharacterRelation, TimelineEvent, FanFiction, FanFictionChapter, AIModelConfig, AIUsageLog）
