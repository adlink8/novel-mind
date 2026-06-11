# Phase 2 决策上下文

> 2026-06-11 复审更新：本上下文已暂停执行，并转为后续 Phase 3 候选输入。原文中“稳定基线”“阅读页缺失”和测试数量等陈述已经漂移，实时状态以 `IMPLEMENTATION-STATUS.md` 与 `.gsd/STATE.md` 为准。

## Security Gate Added 2026-06-11

在恢复本阶段前必须完成：认证授权、SSRF 防护、API key 加密、上传路径 containment、Alembic schema 对齐、正确 reader 路由、章节摘要响应、持久导入 job 和 CI/lint 修复。

Chroma 主存 + pgvector 备用的双写方案不得直接实施。必须先确定权威数据源、deterministic chunk ID、幂等写入、失败补偿和 reconciliation job。

## 阶段信息

- **阶段编号**: 2
- **阶段名称**: 小说导入 + RAG 索引
- **上一阶段**: Phase 1 基础搭建（已完成，审计与启动修复通过）
- **预计工期**: 2-3 周
- **目标**: 用户可以导入小说、在线阅读，并建立 RAG 语义索引

---

## 当前基线状态

### 已完成（可直接复用）

| 组件 | 状态 | 说明 |
|------|------|------|
| FastAPI 后端骨架 | VERIFIED | 12 张表、认证/全局中间件、Alembic 迁移 |
| Next.js 前端骨架 | VERIFIED | App Router、Zustand、shadcn/ui、API 客户端 |
| 小说上传 API | VERIFIED | TXT 上传、编码检测、章节分割、CRUD |
| AI 模型配置 | VERIFIED | CRUD + 连接测试 + 全端点测试覆盖 |
| Docker Compose 数据层 | VERIFIED | PostgreSQL + pgvector、Chroma、Neo4j |
| 自动化测试体系 | VERIFIED | 后端 53 个 pytest + 前端 20 个 Vitest + CI/CD |

### 已有但不完整

| 组件 | 状态 | 缺口 |
|------|------|------|
| TextChunk ORM | 表存在 | 无分块逻辑、无 embedding 写入、无检索 |
| Chroma Docker | 服务跑通 | 无 Python client 代码 |
| pgvector | 扩展已装 | 无向量字段使用、无向量索引查询 |
| 小说阅读页 | MISSING | 无 `novels/[id]` 路由 |

---

## 关键技术决策

### 决策 1：Embedding 方案选型

**选项 A：本地 BGE-large-zh-v1.5（推荐）**
- 优点：零 API 成本、离线可用、中文效果 SOTA
- 缺点：首次加载慢（~1GB）、需 GPU 加速才快
- 部署：Docker 容器化，CPU 推理可接受（100-300ms/文本块）

**选项 B：OpenAI text-embedding-3-small**
- 优点：速度快、无需本地资源
- 缺点：API 成本、数据外发、依赖网络

**结论**：选 A。项目定位是"自托管平台"，本地 Embedding 是核心卖点。MVP 时可用 CPU 推理，后续可加 GPU profile。

### 决策 2：向量存储策略

**方案：Chroma 主存 + pgvector 备用**
- Chroma：负责语义检索（相似度搜索、元数据过滤）
- pgvector：备用路径，与业务数据同库便于事务一致性
- 写入时双写，读取时优先 Chroma

**理由**：
- 路线图要求两者都支持
- Chroma 检索 API 更友好
- pgvector 可作为降级方案（MVP 策略）

### 决策 3：分块策略

**三级分块**：
1. **章节级**：按已有章节分割结果，每章一个 chunk（用于粗粒度检索）
2. **场景级**：章节内按空行/对话分隔切分，300-500 字（用于中等粒度）
3. **段落级**：固定窗口 256 tokens，overlap 50 tokens（用于细粒度）

**索引策略**：
- Chroma collection 按粒度分：`novel_{id}_chapter`、`novel_{id}_scene`、`novel_{id}_paragraph`
- 元数据标注：`chapter_number`, `characters`, `location`, `chunk_type`

### 决策 4：阅读页技术选型

**前端**：
- 路由：`/novels/[id]` 阅读页 + `/novels/[id]/chapters/[chapterId]` 单章阅读
- 组件：章节侧边栏目录 + 主阅读区 + 底部进度条
- 状态：阅读进度 localStorage 持久化

---

## 依赖与风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| BGE 模型下载慢（1GB+） | 首次 Docker 启动慢 | 预下载到 volume，加进度提示 |
| 长文本分块性能 | 大小说（>10MB）处理慢 | 异步后台任务 + 进度反馈 |
| Chroma client API 变动 | 兼容性风险 | 封装 service 层隔离 |
| 前端阅读器体验 | 大章节渲染卡顿 | 虚拟滚动（react-window） |

---

## 验收标准

1. 用户可上传 TXT → 自动分章 → 在线阅读
2. 三级分块后自动建立语义索引
3. 搜索框输入自然语言 → 返回相关文本块 → 点击跳转原文
4. 所有新增代码有 pytest/vitest 覆盖
5. CI/CD 全绿通过
