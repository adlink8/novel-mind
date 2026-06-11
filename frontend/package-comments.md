# NovelMind 前端依赖说明

> package.json 不支持注释，本文档说明各依赖包在项目中的角色。

## dependencies（生产依赖）

| 包名 | 用途 |
|------|------|
| `@base-ui/react` | 无样式基础 UI 组件库，提供可访问的底层组件（对话框、菜单等） |
| `@tanstack/react-query` | 异步状态管理，处理 API 请求的缓存、重试、加载状态 |
| `axios` | HTTP 客户端，用于调用后端 REST API |
| `class-variance-authority` (cva) | CSS 类名变体管理，根据 props 动态生成样式类（与 Tailwind 配合） |
| `clsx` | 条件性 CSS 类名拼接工具 |
| `echarts` | 数据可视化图表库，用于小说数据分析、角色关系图谱可视化 |
| `echarts-for-react` | ECharts 的 React 封装组件 |
| `lucide-react` | 图标库，提供大量 SVG 图标组件（Lucide 图标集） |
| `next` | Next.js 14 框架，提供 SSR/SSG、路由、API Routes 等能力 |
| `react` | React 18 UI 库，前端核心框架 |
| `react-dom` | React DOM 渲染器，将 React 组件渲染到浏览器 |
| `shadcn` | shadcn/ui 组件库（基于 Radix UI + Tailwind），提供高质量预制组件 |
| `tailwind-merge` | 智能合并 Tailwind CSS 类名，解决类名冲突问题 |
| `tw-animate-css` | Tailwind CSS 动画扩展，提供过渡和动画类 |
| `zustand` | 轻量级状态管理库，管理全局应用状态（如当前小说、编辑器状态等） |

## devDependencies（开发依赖）

| 包名 | 用途 |
|------|------|
| `@types/node` | Node.js API 的 TypeScript 类型定义 |
| `@types/react` | React API 的 TypeScript 类型定义 |
| `@types/react-dom` | React DOM API 的 TypeScript 类型定义 |
| `autoprefixer` | PostCSS 插件，自动添加 CSS 浏览器前缀（-webkit-、-moz- 等） |
| `eslint` | JavaScript/TypeScript 代码检查工具 |
| `eslint-config-next` | Next.js 官方 ESLint 配置，包含 Next.js 最佳实践规则 |
| `postcss` | CSS 后处理器，Tailwind CSS 的底层依赖 |
| `tailwindcss` | 原子化 CSS 框架，通过 utility class 快速构建 UI |
| `typescript` | TypeScript 编译器，提供类型检查和代码提示 |
