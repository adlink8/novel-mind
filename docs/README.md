# NovelMind 文档中心

`docs/` 是项目维护者、开发者和使用者阅读的人工文档区。

## 维护规则

- 内容以清晰、稳定、可阅读为目标，不保存 AI 执行游标或临时任务状态。
- 产品目标、架构说明、开发方式、测试方法和部署边界在此维护。
- 完成状态必须引用实际代码、测试或构建证据。
- 日常维护者只需阅读和更新 `README.md`、`IMPLEMENTATION-STATUS.md` 与 `docs/`。
- AI 的 milestone、phase、plan、state、requirements 和 backlog 统一存放在 `.planning/`（GSD 工作流）。

## 文档入口

| 分类 | 文档 | 说明 |
|------|------|------|
| 项目 | `../README.md` | 项目入口、快速开始 |
| 状态 | `../IMPLEMENTATION-STATUS.md` | 权威实现状态 |
| 架构 | `architecture/README.md` | 系统架构设计文档（11 篇 + Mermaid 图） |
| 开发 | `GETTING-STARTED.md` | 本地启动 |
| 开发 | `DEVELOPMENT.md` | 开发约定 |
| 测试 | `TESTING.md` | 测试和质量门槛 |
| 配置 | `CONFIGURATION.md` | 配置与 secrets 规则 |
| 接口 | `API.md` | API 能力和限制 |
| 部署 | `DEPLOYMENT.md` | 部署边界 |
| 产品 | `需求文档.md` | 产品需求 |
| 产品 | `路线图.md` | 产品路线图 |
| 产品 | `竞品调研报告.md` | 竞品分析 |

`.planning/` 是 AI 工作目录，不作为维护者阅读文档的主入口。
