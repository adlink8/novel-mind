# 01 — 系统总览

## 项目定位

NovelMind 是一个 AI 辅助小说理解与同人创作平台。用户上传小说 TXT 文件后，系统完成解析、分章、存储；通过 RAG 管线建立语义索引；支持语义搜索、AI 分析和 AI 创作。

## 核心用户路径

```
用户上传小说
  → 系统编码检测、分章解析、存储
  → RAG 建索引（分块 + embedding + 向量存储）
  → 用户语义搜索 / AI 分析 / AI 创作
  → 系统返回带原文证据的结果
```

## 当前能力边界

### VERIFIED（已实现并验证）

- 账户体系：注册/登录/注销，bcrypt + JWT + HttpOnly Cookie
- 小说导入：TXT 上传、GB18030/Big5/Shift_JIS 多编码检测、章节分割
- 小说阅读：章节侧边栏 + 阅读内容 + 进度条
- 资源隔离：所有资源按 owner 过滤，跨用户返回 404
- AI 模型配置：用户级 OpenAI-compatible 提供商 CRUD，API Key Fernet 加密
- RAG 管线：语义分块、embedding、ChromaDB 向量存储、语义搜索 API
- 安全防护：SSRF 白名单 + DNS/IP 校验、上传文件 containment、响应最小化
- 测试基线：172 pytest + 22 Vitest，全部通过
- CI/静态检查：ESLint 0、Ruff 0、Bandit 中高风险 0、pip-audit 0、npm audit 0

### PARTIAL（部分实现，不完整）

- 阅读进度：已受 owner 隔离，但无多设备历史模型
- 导入进度：ImportJob 模型持久化，大文件流式处理待实现
- AI 路由与成本统计：服务骨架存在，业务端点未接入
- 生产部署：应用拒绝弱密钥，但 TLS/秘密管理/监控由部署环境提供

### MISSING（未实现）

- 混合语义搜索（向量 + 关键词）
- AI 分析与创作（剧情分析、人物图谱、时间线、同人文生成 — 路由返回 501）
- 富文本编辑与 EPUB/Markdown 导出

## 外部依赖

| 组件 | 版本 | 用途 |
|---|---|---|
| PostgreSQL | 16 + pgvector | 主数据库，业务数据 + 向量存储备选 |
| ChromaDB | latest | 向量数据库，RAG 检索 |
| Docker Compose | — | 开发环境服务编排 |
| AI Providers | OpenAI-compatible | LLM / embedding 外部调用 |

## Phase 演进

### Phase 1（v0.1）— 已完成

- 账户体系、小说导入、基础阅读器
- 前端脚手架、基础 CI

### Phase 2（v0.2）— 当前

**02-01（已完成）**：安全修复 — owner 隔离、SSRF 防护、密钥加密、上传安全、响应最小化、ORM 漂移修复

**02-02（已完成）**：RAG 管线核心 — 分块、embedding、ChromaDB 集成、语义搜索 API、索引进度追踪

**02-03（待执行）**：持久化导入任务（worker/租约/幂等重试）、生产依赖审计收尾

### Phase 3（v0.3）— 规划中

- 混合语义搜索
- AI 剧情分析、人物图谱、时间线抽取
- AI 同人文生成
- 富文本编辑与多格式导出

## 当前验证快照（2026-06-12）

| 检查项 | 结果 |
|---|---|
| Backend pytest | 172 passed |
| Frontend Vitest | 22 passed |
| Frontend ESLint + Build | 0 errors, build passed |
| Python pip-audit | 0 vulnerabilities |
| Bandit | 中高风险 0 |
| npm audit | 0 vulnerabilities |
| Alembic | head `f3c8b7b2dbf7`，current/check passed |
| 导入集成测试 | 《龙族Ⅰ·火之晨曦》11 章 / 274,011 字导入成功 |
