# frontend/src/components — 组件层

React 组件，按功能域分目录组织。

## 顶层组件

| 组件 | 文件 | 职责 |
|---|---|---|
| `AuthGate` | `auth-gate.tsx` | 认证门禁 — 检查登录状态，未登录显示登录/注册表单 |
| `AppShell` | `app-shell.tsx` | 响应式应用框架 — 桌面侧栏、移动导航、全局搜索入口 |
| `PageHeader` | `page-header.tsx` | 页面标题区 — eyebrow、标题、说明和操作插槽 |
| `NovelUploadDialog` | `novel-upload-dialog.tsx` | 小说上传对话框 — 文件选择、进度显示 |
| `EmptyState` | `empty-state.tsx` | 空状态占位 — 无数据时的友好提示 |

## 阅读器组件 (`reader/`)

| 组件 | 文件 | 职责 |
|---|---|---|
| `ChapterSidebar` | `reader/chapter-sidebar.tsx` | 章节侧边栏 — 章节目录、切换、当前高亮 |
| `ProgressBar` | `reader/progress-bar.tsx` | 阅读进度条 — 当前章节/总章节、百分比 |
| `ReaderContent` | `reader/reader-content.tsx` | 阅读内容区 — 正文排版、可读宽度和章节空状态 |
| `SearchPanel` | `reader/search-panel.tsx` | 阅读页内搜索抽屉 — Ctrl+F、结果定位、Esc 关闭 |

## 搜索组件 (`search/`)

| 组件 | 文件 | 职责 |
|---|---|---|
| `SearchBar` | `search/search-bar.tsx` | 全局搜索输入、300ms 防抖、Command/Ctrl+K 与下拉预览 |
| `SearchResultCard` | `search/search-result-card.tsx` | 搜索结果、高亮片段、相关度与章节跳转 |

## 分析工作台组件

| 域 | 目录 | 职责 |
|---|---|---|
| 时间线 | `timeline/` | 控件、状态条、chart；overview stages 按剧情密度/章节 seam 分段（非固定 7 段） |
| 人物关系 | `relationships/` | workspace、controls、evidence、hub-centric 关系图 |
| 线索与伏笔 | `clues/` | workspace、controls、evidence、band |

## 设置中心组件 (`settings/`)

| 组件 | 文件 | 职责 |
|---|---|---|
| `SettingsSection` | `settings/settings-section.tsx` | 章回体小节骨架 — 朱砂章节字 + 衬线标题 + 操作插槽 |
| `RoutingSection` | `settings/routing-section.tsx` | 路由策略迷你书架 — 三本书选出策略，API 持久化 |
| `ModelsSection` | `settings/models-section.tsx` | AI 模型管理 — 列表、添加 Dialog、测试连接、设默认、删除 |
| `UsageSection` | `settings/usage-section.tsx` | 用量概览 — `GET /api/usage/summary`，失败显示「暂无数据」 |

富文本编辑器仍未创建。同人文/创作可视化不在本目录。

## UI 基础组件 (`ui/`)

基于 shadcn/ui 的 10 个基础组件：`badge`、`button`、`card`、`dialog`、`dropdown-menu`、`input`、`sheet`、`tabs`、`textarea`、`tooltip`。

使用 `npx shadcn-ui@latest add <name>` 添加新组件。

## 约定

- 组件文件使用 `.tsx`，类型使用 TypeScript
- 使用 Tailwind CSS 原子类，避免内联样式
- 可复用组件放在顶层或子目录，页面专有逻辑留在页面文件中
- 组件状态优先使用 props 传递，跨组件共享用 `stores/`
- 搜索高亮使用 React 节点拆分，不注入原始 HTML
- 视觉基线采用石墨黑、米白与低饱和金色，并保持键盘焦点可见
