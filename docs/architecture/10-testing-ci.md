# 10 — 测试与 CI 架构

NovelMind 的测试体系和持续集成架构。

## 测试层级

```
Browser E2E Tests (待纳入 CI)
  ├── Backend Integration Tests (pytest + pytest-asyncio)
  │     ├── Unit Tests (纯函数、工具函数)
  │     └── API Tests (httpx AsyncClient + 真实数据库)
  └── Frontend Tests (Vitest + React Testing Library)
        ├── Unit Tests (utils、API client)
        └── Component Tests (待补充)
```

## 后端测试

### 框架

| 工具 | 用途 |
|---|---|
| pytest | 测试框架 |
| pytest-asyncio | 异步测试支持 |
| httpx.AsyncClient | FastAPI 端点测试 |
| SQLite / PostgreSQL | 测试数据库 |

### 统计

| 指标 | 值 |
|---|---|
| 测试数量 | **239 non-e2e + 12 RAG e2e** |
| 通过率 | **100%** |
| 验证版本 | Python 3.13.12 |

### 覆盖范围

| 测试模块 | 覆盖内容 | 文件 |
|---|---|---|
| 认证与隔离 | 注册/登录/注销/密码边界/跨用户隔离 | `tests/test_security.py`、`tests/test_novels.py` |
| 小说与导入 | 上传/列表/详情/章节查询/多编码/任务恢复 | `tests/test_novels.py`、`tests/test_import_job.py` |
| 安全 | SSRF 防护/DNS 校验/IPv4/IPv6/owner 隔离 | `tests/test_security.py` |
| 模型与服务 | ORM 约束、模型配置、服务边界 | `tests/test_models.py`、`tests/test_services.py` |
| 分块 | 语义分块算法/三级分块/块类型检测 | `tests/test_chunking.py` |
| 向量存储 | ChromaDB 写入/搜索/删除 | `tests/test_vector_store.py` |
| 索引 | 索引管线端到端/进度追踪 | `tests/test_indexing.py` |
| RAG | 语义搜索/触发索引/状态查询 | `tests/test_rag.py` |
| 混合检索 | 关键词与向量融合/过滤/分页 | `tests/test_hybrid_search.py` |
| RAG 评测 | 数据集/候选/运行/指标/owner 隔离 | `tests/test_eval_models.py`、`tests/test_eval_service.py`、`tests/test_eval_candidates.py`、`tests/test_eval_api.py` |
| RAG E2E | 索引到检索的真实服务链路 | `tests/test_rag_e2e.py` |
| 后续能力骨架 | 分析/人物/时间线/同人文占位契约 | `tests/test_analysis.py`、`tests/test_characters.py`、`tests/test_timeline.py`、`tests/test_fanfiction.py` |

### 运行

```bash
cd backend
source venv/Scripts/activate

# 全部测试
pytest tests/ -v

# 如默认 Windows 临时目录无权限
pytest tests/ -v -p no:cacheprovider --basetemp .test-tmp

# 特定模块
pytest tests/test_novels.py -v
pytest tests/test_security.py -v
pytest tests/test_rag.py -v

# 带覆盖率
pytest tests/ -v --cov=app --cov-report=term
```

## 前端测试

### 框架

| 工具 | 用途 |
|---|---|
| Vitest | 测试框架 |
| React Testing Library | 组件渲染与交互 |
| jsdom | DOM 模拟环境 |

### 统计

| 指标 | 值 |
|---|---|
| 测试数量 | **22** |
| 通过率 | **100%** |

### 覆盖范围

| 测试文件 | 覆盖内容 |
|---|---|
| `lib/api.test.ts` | API 客户端请求参数、响应处理、错误状态 |
| `lib/utils.test.ts` | `cn()` 类名合并、日期格式化 |

当前尚未为 `stores/`、`hooks/`、页面和组件建立自动化测试；这些区域目前由 TypeScript/ESLint、生产构建和浏览器验收兜底。

### 运行

```bash
cd frontend

# 全部测试
npm test

# 监视模式
npm run test:watch

# 带覆盖率
npm test -- --coverage
```

## 静态检查

### Backend

```bash
# Ruff (Python linter)
cd backend
.\.venv\Scripts\python.exe -m ruff check app tests migrations
# 当前: 0 errors

# Bandit (安全扫描)
.\.venv\Scripts\python.exe -m bandit -r app -ll -q
# 当前: 中高风险 0

# pip-audit (依赖漏洞)
.\.venv\Scripts\python.exe -m pip_audit --local --skip-editable
# 2026-06-13 复审: 网络/索引访问超时；不能据此声明当前为 0
```

## 浏览器验收

2026-06-13 已验证登录、工作台、书架、RAG 评测和设置页的 1280px 桌面布局，以及工作台和书架的 390px 移动布局。页面无控制台错误；搜索结果使用安全 React 节点渲染，并跳转到有效的阅读路由。

### Frontend

```bash
# ESLint
cd frontend
npm run lint
# 当前: 0 errors

# npm audit
npm audit --registry=https://registry.npmjs.org
# 当前: 0 vulnerabilities
```

## CI 流程

### GitHub Actions

```yaml
# .github/workflows/backend-tests.yml
name: Backend Tests
on: [push, pull_request]
  branches: [main, master, develop]

jobs:
  test:
    - Checkout
    - Setup Python 3.11
    - Install dependencies
    - Ruff check
    - Bandit scan
    - Alembic check
    - pytest

# .github/workflows/frontend-tests.yml
name: Frontend Tests
on: [push, pull_request]
  branches: [main, master, develop]

jobs:
  test:
    - Checkout
    - Setup Node.js 20
    - npm install
    - ESLint
    - Vitest
    - Next.js build
    - npm audit
```

## VERIFIED 判定标准

```
VERIFIED = 代码存在 + 接入主路径 + 测试覆盖 + CI 通过
```

## 手动验收流程

发布前手动检查：

```
1. Docker 服务启动
   docker compose up -d db chroma

2. 后端启动 + 迁移
   cd backend && alembic upgrade head && uvicorn app.main:app

3. 前端启动
   cd frontend && npm run dev

4. 功能验收
   - 注册新用户 → 登录 → 上传小说 → 阅读章节
   - 导入《龙族Ⅰ·火之晨曦》→ 查看 11 章
   - 添加 AI 模型配置 → 测试连接
   - 触发 RAG 索引 → 混合检索 → 章节跳转
   - 创建评测数据集 → 运行评测 → 查看指标趋势
   - 检查桌面端与 390px 移动端核心页面

5. 安全验收
   - 跨用户访问返回 404
   - 上传非 .txt 文件被拒绝
   - 爬取 API 响应不含 source_path / api_key
```

## 修改后验证

修改任何模块后：

| 变更范围 | 需运行的验证 |
|---|---|
| 后端路由/Service | `pytest tests/` |
| ORM 模型 | `alembic check` + `pytest` |
| 安全相关 | `pytest tests/test_security.py` + `bandit` |
| 前端页面/组件 | `npm test` + `npm run lint` + `npm run build` |
| 依赖变更 | `pip-audit` + `npm audit` |
