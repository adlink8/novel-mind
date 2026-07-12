# 09 — 前端架构

Next.js 16 App Router 前端应用，基于 React 19 + TypeScript + Tailwind CSS。

## 技术栈

| 技术 | 版本 | 用途 |
|---|---|---|
| Next.js | 16.3.0-canary.6 | 全栈框架（App Router + RSC） |
| React | 19 | UI 框架 |
| TypeScript | 5.x | 类型安全 |
| Tailwind CSS | 3.4.x | 原子化样式 |
| shadcn/ui | latest | UI 组件库 |
| Zustand | 4.5.x | 全局状态管理 |
| Axios | 1.x | HTTP 客户端 |
| Vitest | 4.x | 单元测试 |
| React Testing Library | latest | 组件测试 |

## 目录结构

```
frontend/src/
├── app/                    # Next.js App Router 页面
│   ├── layout.tsx          # 根布局（认证门禁 + 导航栏）
│   ├── page.tsx            # 首页（小说列表）
│   ├── globals.css         # 全局样式
│   ├── novels/
│   │   ├── page.tsx        # 小说列表页
│   │   └── [id]/
│   │       └── page.tsx    # 小说详情/阅读页（动态路由）
│   ├── search/
│   │   └── page.tsx        # 全局混合搜索结果页
│   ├── eval/
│   │   └── page.tsx        # RAG 评测管理与 ECharts 可视化
│   ├── settings/
│   │   └── page.tsx        # AI 模型设置页
│   └── writing/
│       └── page.tsx        # 写作页（骨架）
├── components/             # React 组件
│   ├── auth-gate.tsx       # 认证门禁
│   ├── app-shell.tsx       # 响应式应用壳（桌面侧栏 + 移动底栏）
│   ├── page-header.tsx     # 页面容器与统一标题层级
│   ├── novel-card.tsx      # 小说卡片
│   ├── novel-upload-dialog.tsx  # 上传对话框
│   ├── empty-state.tsx     # 空状态占位
│   ├── reader/             # 阅读器组件
│   │   ├── chapter-sidebar.tsx
│   │   ├── reader-content.tsx
│   │   ├── progress-bar.tsx
│   │   └── search-panel.tsx
│   ├── search/             # 搜索栏与结果卡片
│   └── ui/                 # shadcn/ui 基础组件（10个）
├── lib/                    # 工具与 API 客户端
│   ├── api.ts              # Axios API 封装（7.6KB）
│   └── utils.ts            # 通用工具函数
├── stores/                 # Zustand 全局状态
│   ├── aiConfigStore.ts    # AI 模型配置状态
│   └── novelStore.ts       # 小说列表状态
├── hooks/                  # 自定义 React Hooks
│   ├── use-novels.ts       # 小说数据获取
│   └── use-ai-models.ts    # AI 模型数据获取
└── __tests__/              # 前端测试
    └── setup.ts
```

## 页面路由

| 路径 | 页面 | 认证 | 状态 |
|---|---|---|---|
| `/` | 首页（小说列表） | 需要登录 | VERIFIED |
| `/novels` | 小说列表 + 上传 | 需要登录 | VERIFIED |
| `/novels/[id]` | 小说详情/阅读器 | 需要登录 | VERIFIED |
| `/search` | 跨小说混合搜索结果 | 需要登录 | VERIFIED |
| `/eval` | RAG 评测数据、运行和可视化 | 需要登录 | VERIFIED |
| `/settings` | AI 模型设置 | 需要登录 | VERIFIED |
| `/writing` | 写作页 | 需要登录 | SKELETON（骨架） |

## 视觉系统

- 方向：文学编辑台 + AI 研究工作区，避免通用后台模板感。
- 色彩：纸张暖白背景、墨黑正文、陶土橙主操作色、靛蓝辅助色。
- 层级：页面统一使用 `PageContainer` 和 `PageHeader`；卡片使用 `paper-surface`。
- 图标：交互图标统一使用 Lucide，不以 emoji 充当按钮图标。
- 响应式：桌面使用浮动侧栏，移动端使用顶部品牌栏和六项底部导航。
- 无障碍：提供 skip link、当前路由 `aria-current`、表单标签和 `prefers-reduced-motion` 降级。

## 认证流程

```
用户访问 → layout.tsx → AuthGate
  ├── 未登录 → 显示登录/注册表单
  └── 已登录 → 渲染子页面

AuthGate 通过 Cookie 中的 JWT 自动验证，无需手动传递 token。
```

**来源**: `frontend/src/components/auth-gate.tsx`、`frontend/src/app/layout.tsx`

## API 客户端

**来源**: `frontend/src/lib/api.ts`（约 8KB）

```typescript
// Axios 实例，携带 Cookie 凭据
const client = axios.create({
  baseURL: '/api',
  withCredentials: true,
});

// 封装的后端调用
export const authApi / novelsApi / aiModelsApi / searchApi = {
  // 认证
  register(data) → POST /api/auth/register
  login(data) → POST /api/auth/login
  logout() → POST /api/auth/logout
  getMe() → GET /api/auth/me

  // 小说
  getNovels() → GET /api/novels
  getNovel(id) → GET /api/novels/{id}
  uploadNovel(file) → POST /api/novels/upload
  deleteNovel(id) → DELETE /api/novels/{id}
  getChapter(novelId, chapterId) → GET /api/novels/{id}/chapters/{cid}

  // AI 模型
  getModels() → GET /api/models
  createModel(data) → POST /api/models
  updateModel(id, data) → PUT /api/models/{id}
  deleteModel(id) → DELETE /api/models/{id}
  testModel(id) → POST /api/models/{id}/test
  setDefaultModel(id) → POST /api/models/{id}/set-default

  // 混合搜索
  global(query, topK) → POST /api/search
  inNovel(novelId, query, topK) → POST /api/search/novels/{id}
};
```

评测页面当前直接使用底层 `api` Axios 实例访问 `/eval/*`，尚未拆出独立的 `evalApi` 契约模块。

## 状态管理

**Zustand** stores：

```typescript
// aiConfigStore — AI 模型配置
{
  models: AIModelConfig[],       // 用户的所有模型配置
  defaultModel: AIModelConfig?,  // 当前默认模型
  isLoading: boolean,
  error: string?,
  // 方法: fetchModels, addModel, updateModel, deleteModel, testModel, setDefault
}

// novelStore — 小说列表
{
  novels: Novel[],               // 当前用户的所有小说
  selectedNovel: Novel?,         // 当前选中
  isLoading: boolean,
  error: string?,
  // 方法: fetchNovels, addNovel, updateNovel, removeNovel
}
```

## 组件树

```
Layout (layout.tsx)
├── AuthGate (auth-gate.tsx)
│   ├── [未登录] LoginForm / RegisterForm
│   └── [已登录]
│       └── AppShell (app-shell.tsx)
│           ├── Desktop Sidebar / Mobile Navigation
│           └── Page Content
│           ├── HomePage (page.tsx)
│           │   └── NovelCard[] (novel-card.tsx)
│           ├── NovelsPage (novels/page.tsx)
│           │   ├── NovelUploadDialog (novel-upload-dialog.tsx)
│           │   └── NovelCard[]
│           ├── NovelDetailPage (novels/[id]/page.tsx)
│           │   ├── ChapterSidebar (reader/chapter-sidebar.tsx)
│           │   ├── ReaderContent (reader/reader-content.tsx)
│           │   ├── ProgressBar (reader/progress-bar.tsx)
│           │   └── SearchPanel (reader/search-panel.tsx)
│           ├── SearchPage (search/page.tsx)
│           │   ├── SearchBar (search/search-bar.tsx)
│           │   └── SearchResultCard[] (search/search-result-card.tsx)
│           ├── EvalPage (eval/page.tsx) — 数据集/运行/指标/趋势
│           ├── SettingsPage (settings/page.tsx)
│           └── WritingPage (writing/page.tsx) — skeleton
```

## Web 优先策略

```
先保证 Next.js Web 体验 → 再考虑 PWA / Capacitor / Tauri 等多端扩展
```

当前为纯 Web 应用，无 PWA。桌面、平板和 390px 移动端均有明确导航与内容避让。

## 构建与验证

```bash
cd frontend

# 开发
npm run dev

# 生产构建
npm run build

# Lint
npm run lint          # ESLint（当前 0 错误）

# 测试
npm test              # Vitest（22 passed）

# 安全审计
npm audit
```

## 修改后验证

修改前端代码后，必须：

1. `npm run lint` — ESLint 通过
2. `npm test` — 22 个测试通过
3. `npm run build` — 生产构建通过
4. 手动测试关键路径：登录 → 上传 → 阅读/页内搜索 → 全局搜索 → RAG 评测 → 设置
