# NovelMind 文档中心

`docs/` 是项目维护者、开发者和使用者阅读的人工文档区。

## 维护规则

- 内容以清晰、稳定、可阅读为目标，不保存 AI 执行游标或临时任务状态。
- 产品目标、架构说明、开发方式、测试方法和部署边界在此维护。
- 完成状态必须引用实际代码、测试或构建证据。
- 日常维护者只需阅读和更新 `README.md`、`IMPLEMENTATION-STATUS.md` 与 `docs/`。
- AI 的 milestone、phase、plan、state、requirements 和 backlog 统一存放在 `.gsd/`。

## 文档入口

- `../README.md`：项目入口
- `../IMPLEMENTATION-STATUS.md`：实际实现状态
- `ARCHITECTURE.md`：当前代码架构与边界
- `GETTING-STARTED.md`：本地启动
- `DEVELOPMENT.md`：开发约定
- `TESTING.md`：测试和质量门槛
- `CONFIGURATION.md`：配置与 secrets 规则
- `API.md`：API 能力和限制
- `DEPLOYMENT.md`：部署边界
- `需求文档.md`、`路线图.md`：产品规划

`.gsd/` 是 AI 工作目录，不作为维护者阅读文档的主入口。
