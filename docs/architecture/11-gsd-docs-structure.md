# 11 — GSD 与文档协同

说明 GSD、架构文档、模块 README 之间的分工、更新规则和引用关系。

## 文档层级

```
CI / 测试结果 ← 最终事实来源
    ↑
代码实现 ← 第二事实来源
    ↑
GSD 状态（.planning/STATE.md） ← 项目演进的"当前光标"
    ↑
模块 README（各子目录） ← 模块的职责、边界、入口
    ↑
架构文档（docs/architecture/） ← 系统结构、模块关系、数据流
    ↑
根 README ← 项目总览、快速开始
    ↑
旧规划文档 ← 历史参考，不作为当前事实
```

## 各文档的责任

| 文档 | 责任 | 受众 | 何时更新 |
|---|---|---|---|
| 根 `README.md` | 项目总览、快速开始、验证命令 | 新开发者 | 技术栈变更、启动流程变更 |
| `IMPLEMENTATION-STATUS.md` | 实现状态审计 | 所有开发者 | 审计事件后 |
| GSD（`.planning/`） | 项目阶段、演进、任务、当前状态 | AI Agent、维护者 | 每个 milestone/slice 完成时 |
| 架构文档（`docs/architecture/`） | 系统结构、模块关系、数据流 | 开发者、AI Agent | 模块增删、架构重构 |
| 模块 README（`**/README.md`） | 模块职责、边界、入口、测试 | 进入该模块的开发者 | 模块行为变更时 |
| 测试文档 | 验证方式、测试结果 | QA、CI | 测试框架或覆盖变更时 |
| 产品文档（`docs/` 其他） | 需求、路线图、竞品分析 | 产品、管理者 | 需求变更时 |

## GSD 目录结构

```
.planning/
├── config.json         # 活跃 milestone、active plan、状态标签定义
├── PROJECT.md          # 核心价值声明、里程碑历史、执行规则
├── STATE.md            # 当前执行位置、已完成/待完成工作、验证快照
├── ROADMAP.md          # 里程碑路线图
├── REQUIREMENTS.md     # 需求文档
├── README.md           # GSD 目录说明
├── phases/             # 各阶段的 plan + summary
│   ├── 01-audit-and-startup-fixes/
│   └── 02-security-and-architecture-remediation/
└── backlog/            # 待激活计划
    └── 03-novel-import-rag/
```

## 架构文档目录结构

```
docs/architecture/
├── README.md                   # 导航与元规则
├── 01-system-overview.md       # 系统总览
├── 02-module-map.md            # 模块地图
├── 03-data-model.md            # 数据模型
├── 04-request-flow.md          # 请求与业务流
├── 05-import-pipeline.md       # 导入管线
├── 06-rag-pipeline.md          # RAG 管线
├── 07-auth-security.md         # 认证与安全
├── 08-ai-model-layer.md        # AI 模型层
├── 09-frontend-architecture.md # 前端架构
├── 10-testing-ci.md            # 测试与 CI
├── 11-gsd-docs-structure.md    # 本文档
└── diagrams/                   # Mermaid 图源文件
```

## 更新规则

```
修改模块行为 → 更新对应模块 README
修改系统结构/模块增删 → 更新 architecture/ 对应文档
完成 milestone / slice → 更新 .planning/STATE.md + .planning/phases/
修改安全边界 → 更新 07-auth-security.md + 补充测试
修改数据库 schema → 更新 03-data-model.md + 生成 Alembic 迁移
新增/删除外部依赖 → 更新 01-system-overview.md
```

## 不重复写入

- 模块 README 不重复 architecture/ 的全局架构描述
- architecture/ 不重复 GSD 的详细任务分解
- GSD 不重复代码实现细节
- 根 README 不重复 architecture/ 的详细模块关系

## GSD 引用架构文档

GSD 可通过以下方式引用架构文档作为系统结构事实来源：

```markdown
<!-- .planning/STATE.md 或 PLAN.md 中 -->
系统结构以 docs/architecture/ 为准。
当前架构状态见 docs/architecture/02-module-map.md。
```

当前 GSD 中的架构描述应视为过渡期内容，最终应迁移到 `docs/architecture/` 并由 GSD 引用。
