# NovelMind 架构设计文档

系统架构的全局视角文档。回答"系统由哪些模块组成、模块之间如何协作、数据如何流转"。

## 文档导航

| 文档 | 内容 | 面向读者 |
|---|---|---|
| [01 系统总览](01-system-overview.md) | 项目定位、能力边界、外部依赖、用户路径、Phase 演进 | 所有人 |
| [02 模块地图](02-module-map.md) | 所有核心模块清单、职责、文件、状态 | 开发者 / AI Agent |
| [03 数据模型](03-data-model.md) | 核心实体、ER 关系、owner 隔离、存储分布 | 开发者 / AI Agent |
| [04 请求与业务流](04-request-flow.md) | 主要请求链路的时序图与说明 | 开发者 |
| [05 小说导入管线](05-import-pipeline.md) | TXT 上传到入库的完整流程 | 开发者 |
| [06 RAG 管线](06-rag-pipeline.md) | 分块 → embedding → 向量存储 → 检索链路 | 开发者 / AI Agent |
| [07 认证与安全架构](07-auth-security.md) | JWT、bcrypt、owner 隔离、加密、SSRF 防护 | 安全审计 / 开发者 |
| [08 AI 模型层](08-ai-model-layer.md) | AI 提供商配置、Key 加密、调用路由 | 开发者 |
| [09 前端架构](09-frontend-architecture.md) | 页面路由、组件树、状态管理、API 客户端 | 前端开发者 |
| [10 测试与 CI](10-testing-ci.md) | 测试体系、CI 流程、验收标准 | 开发者 / QA |
| [11 GSD 与文档协同](11-gsd-docs-structure.md) | GSD、架构文档、模块 README 的分工与更新规则 | 维护者 / AI Agent |

## 事实优先级

```
CI / 测试结果 > 代码实现 > GSD 状态 > 模块 README > 根 README > 旧规划文档
```

架构文档中的所有关键结论都关联到实际代码文件路径。

## 模块状态标注

| 状态 | 含义 |
|---|---|
| **VERIFIED** | 有代码、有测试、接入主路径，基本可用 |
| **PARTIAL** | 有部分代码，但功能/测试/接入不完整 |
| **MISSING** | 只有文档或 TODO，尚未实现 |
| **PLANNED** | 后续阶段规划，不应被当前 Agent 当成已完成能力 |

## 每篇文档的回答

每篇架构文档应覆盖：

- 这个模块负责什么？
- 不负责什么？
- 上游是谁？下游是谁？
- 入口文件在哪里？测试文件在哪里？
- 修改后应该跑什么命令验证？
- 哪些地方禁止随意改？

## Mermaid 图索引

| 图 | 位置 |
|---|---|
| 系统上下文图 | `diagrams/system-context.mmd` 或 [01 系统总览](01-system-overview.md) |
| 容器视图 | `diagrams/container-view.mmd` |
| RAG 流程图 | `diagrams/rag-flow.mmd` 或 [06 RAG 管线](06-rag-pipeline.md) |
| 导入流程图 | `diagrams/import-flow.mmd` 或 [05 导入管线](05-import-pipeline.md) |
| 认证流程图 | `diagrams/auth-flow.mmd` 或 [07 认证与安全](07-auth-security.md) |

## 版本

- 创建日期：2026-06-12
- 对应里程碑：v0.2 — 安全与架构修复
- 维护规则：修改系统结构时更新对应架构文档
