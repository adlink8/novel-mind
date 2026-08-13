# NovelMind Wiki

> 让 AI 读懂你的故事 —— 结构分析、语义检索与叙事记忆。

## 目录

| 章节 | 内容 |
|---|---|
| [项目概览](index.md) | 这是什么、能做什么、技术栈 |
| [用户指南](user-guide.md) | 导入小说、阅读分析、检索对话 |
| [导入管线](import-pipeline.md) | 编码检测 → 章节拆分 → 分块建索引 |
| [时间线分析](timeline-analysis.md) | 逐章提取、跨章归并、因果边 |
| [叙事记忆 (NM)](narrative-memory.md) | 四层知识结构、Claims 类型、Builder 流水线 |
| [人物关系](relationships.md) | 候选包、语义判决、门控接受 |
| [读者聊天](reader-chat.md) | 证据检索、上下文组装、剧透控制 |
| [检索与评测 (RAG)](search-and-rag.md) | 语义搜索、混合搜索、评测闭环 |
| [前端架构](frontend.md) | 结构工作台、3D 翻页书、动效系统 |
| [架构深度解析](architecture-deep-dive.md) | 管线细节、成本模型、错误处理、防护体系 |
| [部署指南](../DEPLOYMENT.md) | Docker、环境变量、启动脚本 |

## 项目概览

**NovelMind** 是一个 AI 辅助小说理解与分析平台。它不是"AI 替你写小说"——而是**让 AI 帮助你读懂一本书**：导入长篇文本后，系统自动建立可检索的故事记忆，沿着时间线、人物关系与原文证据完成理解、评测与再创作。

### 核心能力矩阵

| 能力 | 状态 | 说明 |
|---|---|---|
| TXT 导入 + 自动分章 | ✅ | 多编码自动检测，按中英文标题切开 |
| 语义检索（全文搜索） | ✅ | 导入即建向量索引，支持语义搜索 |
| 时间线分析 | ✅ | 每章 AI 提取事件 → 跨章归并 → 因果边 |
| 人物关系图 | ✅ | AI 判断人物关系类型 + gates 自动或人工接受 |
| 线索与伏笔 | ✅ | 检测伏笔埋设与回收的生命周期 |
| 叙事记忆 (NM) | ⚠️ 部分 | chapter_state 已完成，弧/卷/全局构建搁置 |
| RAG 评测 (Eval) | ⚠️ 部分 | 10/100 题 confirmed，指标为 0 |
| 分支创作 | 🚧 未开始 | 从原作分支点开始新叙事 |

### 技术栈

```
前端：     Next.js 16 (canary) + Tailwind CSS 3 + base-ui 1.5 + ECharts + react-query
后端：     Python 3.12 + FastAPI + SQLAlchemy 2.0 async + PostgreSQL 16
AI：       OpenAI / Anthropic / Gemini AI Studio / Ollama / OpenAI-compatible（可配置）
向量库：   ChromaDB（本地嵌入）
搜索：     BM25 + 向量混合搜索
```

## 后端架构层次

```
┌─────────────────────────────────────┐
│  API 路由层（FastAPI）               │
│  /api/auth, /api/novels, /api/timeline, /api/search, /api/reader-chat …  │
├─────────────────────────────────────┤
│  Service 业务逻辑层                   │
│  novel_service / import_service / timeline / relationships / nm / reader_chat  │
├─────────────────────────────────────┤
│  Models ORM 层（SQLAlchemy）          │
│  Novel / Chapter / TextChunk / TimelineEvent / NmStructureNode / ReaderMessage …  │
├─────────────────────────────────────┤
│  Core 基础设施层                      │
│  database.py / security.py / ai_service.py / vector_store.py / chunking_service.py  │
└─────────────────────────────────────┘
```

## 前端架构层次

```
┌─────────────────────────────────────┐
│  Pages / Routes                      │
│  / (首页 3D书)  /novels (书架)  /analysis (分析工作台)  /search /eval /settings  │
├─────────────────────────────────────┤
│  Components                          │
│  ui/* (shadcn-like 基元) / FlipBook / StructureTree / TimelineChart / NovelCard  │
├─────────────────────────────────────┤
│  State + API                         │
│  Zustand stores / react-query / lib/api.ts → backend API  │
└─────────────────────────────────────┘
```

## 数据流（单小说）

```
TXT 上传 → 导入任务 → 编码检测 → 章节拆分 → 分块 → 向量嵌入 → ChromaDB
                                                       ↓
                                              ← ready（可阅读/搜索）
用户点「开始分析」→ 时间线 worker → 逐章事件提取 → 跨章归并 → 发布 active
                              → 关系 worker（时间线完成后自动触发）
                              → 线索 worker（并行）
                              → NM builder（可选，预算控制）
用户阅读聊天 → 选择文字 → 证据检索（原文+时间线+关系）→ AI 生成引用答案
```
