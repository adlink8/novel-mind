# backend/tests — 后端测试

pytest + pytest-asyncio 异步测试套件，覆盖全部核心模块。

## 统计

- **测试数量**: 187 个（非 e2e），12 个 e2e，全部通过
- **框架**: pytest + pytest-asyncio + httpx AsyncClient
- **数据库**: 测试用独立 PostgreSQL 数据库（通过 `test` 配置）

## 运行

```bash
cd backend
source venv/Scripts/activate

# 全部测试
pytest tests/ -v

# 按模块运行
pytest tests/test_auth.py -v
pytest tests/test_novels.py -v
pytest tests/test_rag.py -v
pytest tests/test_security.py -v

# 带覆盖率
pytest tests/ -v --cov=app --cov-report=term
```

## 覆盖范围

| 测试模块 | 覆盖内容 |
|---|---|
| 认证 | 注册 / 登录 / 注销 / token 刷新 / 密码边界 |
| 小说 | 上传 / 列表 / 详情 / 删除 / 多编码 / 章节查询 |
| 安全 | SSRF 防护 / 密钥加密 / 权限隔离 / 文件 containment |
| 分块 | 语义分块算法 / 三级分块 / 块类型检测 |
| 索引 | 索引管线端到端 / 进度追踪 |
| 向量存储 | ChromaDB 写入 / 搜索 / 删除 |
| RAG | 语义搜索 / 触发索引 / 索引进度查询 |

## CI

通过 GitHub Actions 自动运行（`.github/workflows/backend-tests.yml`），PR 门禁。
