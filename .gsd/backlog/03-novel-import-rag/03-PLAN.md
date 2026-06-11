# Phase 2 执行计划

> 状态：**BLOCKED / DEFERRED**。本文件保留 2026-06-11 前的“小说导入 + RAG”工作草案。当前 `/gsd auto` 不执行本文件；先完成 `.gsd/phases/02-security-and-architecture-remediation/02-01-PLAN.md` 至 `02-03-PLAN.md`。安全门槛通过后，再复审并激活本计划。

## Steps

1. 完成安全与架构修复 milestone。
2. 重新验证导入、reader、迁移和任务队列基线。
3. 确定 Chroma/pgvector 权威存储、幂等 chunk ID、重试和 reconciliation 策略。
4. 按 Wave 1-4 实施 RAG 功能；每个 implementation slice 最后执行 **Test, Fix, and Confirm**。

## Must-Haves

- 不使用 FastAPI `BackgroundTasks` 作为不可丢失索引任务的最终方案。
- 双写前必须定义事务边界、幂等键、失败补偿和一致性巡检。
- 所有新增 schema 先有 Alembic migration，并在 PostgreSQL 验证。
- 列表 API 不携带章节全文，索引不阻塞上传请求。

## Verification

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic check
.\.venv\Scripts\python.exe -m pytest -q
cd ..\frontend
npm test -- --run
npm run build
npm run lint
```

## 阶段目标

用户可导入小说、在线阅读，并建立 RAG 语义索引。

---

## Wave 1：小说阅读页 + 导入完善（第 1 周前半）

### 任务 1.1：小说阅读页前端
**负责人**: frontend
**依赖**: 无（已有 novels API）
**验收**:
- `/novels/[id]` 路由可访问
- 章节侧边栏目录（可折叠）
- 主阅读区展示章节内容
- 底部阅读进度条
- 阅读进度 localStorage 记忆

### 任务 1.2：后端章节内容 API 优化
**负责人**: backend
**依赖**: 无
**验收**:
- `GET /novels/{id}/chapters/{chapter_id}` 返回完整内容
- `GET /novels/{id}/chapters` 列表含字数和阅读进度字段
- 新增 `PATCH /novels/{id}/progress` 更新阅读进度

### 任务 1.3：导入管线增强
**负责人**: backend
**依赖**: 任务 1.2
**验收**:
- 大文件（>5MB）上传支持流式处理
- 导入进度反馈（WebSocket 或轮询）
- 支持更多编码格式（GB18030、Big5）
- 作者/类型元信息推断（简单规则）

---

## Wave 2：Embedding 服务 + 三级分块（第 1 周后半 ~ 第 2 周）

### 任务 2.1：BGE Embedding 服务封装
**负责人**: backend
**依赖**: Docker Compose embedding 服务
**验收**:
- `app/services/embedding_service.py` 封装 BGE 调用
- 支持 batch embedding（批量文本 → 向量数组）
- 向量维度 1024（BGE-large-zh-v1.5）
- 单测：mock BGE 调用，验证输入输出格式

### 任务 2.2：三级分块引擎
**负责人**: backend
**依赖**: 任务 2.1
**验收**:
- `app/services/chunk_service.py` 实现三级分块
  - 章节级：每章一个 chunk
  - 场景级：按空行/对话切分，300-500 字
  - 段落级：固定窗口 256 tokens，overlap 50
- 元数据标注：chapter_number、characters、location、chunk_type
- 单测：多种文本结构的正确分块

### 任务 2.3：Chroma 向量存储封装
**负责人**: backend
**依赖**: 任务 2.2
**验收**:
- `app/services/chroma_service.py` 封装 CRUD
- collection 命名：`novel_{novel_id}_{granularity}`
- 支持元数据过滤查询
- 双写 pgvector 备用路径（`text_chunks` 表写入 embedding 向量）
- 单测：mock Chroma client，验证 collection 操作

### 任务 2.4：小说索引后台任务
**负责人**: backend
**依赖**: 任务 2.3
**验收**:
- 上传小说后自动触发分块 + embedding + 写入
- 异步执行（FastAPI BackgroundTasks 或 Celery 占位）
- 索引状态跟踪（`novels.indexing_status` 字段）
- 错误处理和重试机制

---

## Wave 3：混合语义搜索（第 2 周后半 ~ 第 3 周）

### 任务 3.1：语义搜索 API
**负责人**: backend
**依赖**: 任务 2.4
**验收**:
- `POST /search/semantic` 接收 query → 返回相关 chunks
- 参数：novel_id（可选，限定小说）、granularity（章节/场景/段落）
- 返回：文本片段 + 相似度分数 + 元数据 + 跳转链接
- 混合策略：向量相似度 + 关键词 BM25（PostgreSQL full-text search）

### 任务 3.2：搜索前端组件
**负责人**: frontend
**依赖**: 任务 3.1
**验收**:
- 全局搜索框（顶栏）
- 搜索结果页（小说内 / 全局）
- 结果高亮显示匹配片段
- 点击跳转对应章节阅读位置

### 任务 3.3：阅读页搜索集成
**负责人**: frontend
**依赖**: 任务 3.2
**验收**:
- 阅读页内搜索（Ctrl+F 增强版）
- 搜索结果侧边栏展示
- 点击结果滚动到对应位置并高亮

---

## Wave 4：集成测试与文档（第 3 周后半）

### 任务 4.1：端到端测试
**负责人**: backend + frontend
**依赖**: Wave 1-3 全部完成
**验收**:
- 上传测试小说 → 自动索引 → 语义搜索 → 点击跳转阅读（全流程 pytest）
- 前端 Vitest：搜索组件、阅读页交互
- 性能测试：1000 个 chunks 的检索延迟 < 500ms

### 任务 4.2：CI/CD 适配
**负责人**: DevOps
**依赖**: 任务 4.1
**验收**:
- GitHub Actions 加入 embedding 服务容器
- 测试用例在 CI 中通过（使用 mock 或轻量模型）

### 任务 4.3：文档更新
**负责人**: 全员
**依赖**: 无
**验收**:
- `IMPLEMENTATION-STATUS.md` 更新 Phase 2 状态
- API 文档（Swagger）自动更新
- 新增模块的 README 注释

---

## 执行顺序图

```
Week 1          Week 2          Week 3
├─ 1.1 阅读页    ├─ 2.1 Embedding ├─ 3.1 语义搜索 API
├─ 1.2 章节 API  ├─ 2.2 三级分块   ├─ 3.2 搜索前端
├─ 1.3 导入增强  ├─ 2.3 Chroma    ├─ 3.3 阅读页搜索
│               ├─ 2.4 索引任务   ├─ 4.1 端到端测试
│               │               ├─ 4.2 CI/CD
│               │               └─ 4.3 文档
```

---

## 关键路径

**最长路径**: 1.2 → 2.2 → 2.3 → 2.4 → 3.1 → 3.2 → 4.1

**可并行**:
- 1.1 阅读页 与 1.2/1.3 后端并行
- 3.2 搜索前端 与 3.3 阅读页搜索 可部分并行

---

## 退出条件

Phase 2 标记为完成的条件：
1. 所有任务验收标准通过
2. 后端 pytest ≥ 70 个用例（新增 ≥ 17 个）
3. 前端 vitest ≥ 30 个用例（新增 ≥ 10 个）
4. CI/CD 全绿
5. `IMPLEMENTATION-STATUS.md` Phase 2 状态更新为 VERIFIED
