# frontend/src/app — 页面层

Next.js 16 App Router 页面路由，每个文件夹对应一个 URL 路径段。

## 路由

| 路径 | 文件 | 职责 |
|---|---|---|
| `/` | `page.tsx` | 工作台首页 — 项目概览、最近项目、快捷入口 |
| `/` | `layout.tsx` | 根布局 — 认证门禁（AuthGate）与响应式应用壳（AppShell） |
| `/` | `globals.css` | 全局样式 — Tailwind CSS + 自定义主题变量 |
| `/novels` | `novels/page.tsx` | 小说列表页 — 展示、上传、搜索 |
| `/novels/[id]` | `novels/[id]/page.tsx` | 小说详情/阅读页 — 动态路由，章节侧边栏 + 阅读内容 |
| `/search` | `search/page.tsx` | 全局混合搜索结果页 — 搜索栏、结果卡片、空/错/加载状态 |
| `/eval` | `eval/page.tsx` | RAG 评测管理 — 数据集、运行、指标对比与趋势图 |
| `/analysis` | `analysis/page.tsx` | 分析工作台 — timeline / relationships / clues progressive 工作区 |
| `/settings` | `settings/page.tsx` | 设置中心 — 账户退出、路由策略（API 持久化）、模型 CRUD/测试连接、用量概览；组装 `components/settings/` 四个区块 |
| `/writing` | `writing/page.tsx` | 创作中心占位 — FlipBook 导览书 + 三步路径书页 + Planned 草稿区，真实创作能力留待后续里程碑 |

## 认证流程

```
用户访问 → layout.tsx → AuthGate 检查
  ├── 未登录 → 显示登录/注册表单
  └── 已登录 → AppShell 导航 → 渲染子页面
```

AuthGate 通过 Cookie 中的 JWT 自动验证，无需手动传递 token。

## 约定

- 使用 App Router（非 Pages Router），文件即路由
- `layout.tsx` 是服务端组件；多数 `page.tsx` 因交互和浏览器状态使用 `'use client'`，纯组装的页面（`settings`、`writing`）保持服务端组件，交互下放给客户端子组件
- 小说列表和模型设置通过 `hooks/`；搜索、阅读和评测页面直接调用 `lib/api.ts`
- 全局状态通过 `stores/` 中的 store 管理
- 页面标题和操作区优先复用 `PageHeader`；桌面导航与移动导航统一由 `AppShell` 管理
- 核心页面需同时检查桌面端和 390px 移动端布局
