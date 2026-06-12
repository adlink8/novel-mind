# frontend/src/components — 组件层

React 组件，按功能域分目录组织。

## 顶层组件

| 组件 | 文件 | 职责 |
|---|---|---|
| `AuthGate` | `auth-gate.tsx` | 认证门禁 — 检查登录状态，未登录显示登录/注册表单 |
| `NovelCard` | `novel-card.tsx` | 小说卡片 — 封面、标题、作者、状态标签 |
| `NovelUploadDialog` | `novel-upload-dialog.tsx` | 小说上传对话框 — 文件选择、进度显示 |
| `EmptyState` | `empty-state.tsx` | 空状态占位 — 无数据时的友好提示 |

## 阅读器组件 (`reader/`)

| 组件 | 文件 | 职责 |
|---|---|---|
| `ChapterSidebar` | `reader/chapter-sidebar.tsx` | 章节侧边栏 — 章节目录、切换、当前高亮 |
| `ProgressBar` | `reader/progress-bar.tsx` | 阅读进度条 — 当前章节/总章节、百分比 |
| `ReaderContent` | `reader/reader-content.tsx` | 阅读内容区 — 正文渲染、字体大小调节 |

## UI 基础组件 (`ui/`)

基于 shadcn/ui 的 10 个基础组件：`badge`、`button`、`card`、`dialog`、`dropdown-menu`、`input`、`sheet`、`tabs`、`textarea`、`tooltip`。

使用 `npx shadcn-ui@latest add <name>` 添加新组件。

## 预留子模块

| 目录 | 状态 | 计划用途 |
|---|---|---|
| `editor/` | 空目录 | 富文本编辑器组件（v0.3） |
| `timeline/` | 空目录 | 时间线可视化组件（v0.3） |
| `character-graph/` | 空目录 | 人物关系图可视化组件（v0.3） |

## 约定

- 组件文件使用 `.tsx`，类型使用 TypeScript
- 使用 Tailwind CSS 原子类，避免内联样式
- 可复用组件放在顶层或子目录，页面专有逻辑留在页面文件中
- 组件状态优先使用 props 传递，跨组件共享用 `stores/`
