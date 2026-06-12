# frontend/src/app — 页面层

Next.js 16 App Router 页面路由，每个文件夹对应一个 URL 路径段。

## 路由

| 路径 | 文件 | 职责 |
|---|---|---|
| `/` | `page.tsx` | 首页 — 小说列表展示，未登录跳转登录 |
| `/` | `layout.tsx` | 根布局 — 认证门禁（AuthGate）、全局导航栏 |
| `/` | `globals.css` | 全局样式 — Tailwind CSS + 自定义主题变量 |
| `/novels` | `novels/page.tsx` | 小说列表页 — 展示、上传、搜索 |
| `/novels/[id]` | `novels/[id]/page.tsx` | 小说详情/阅读页 — 动态路由，章节侧边栏 + 阅读内容 |
| `/settings` | `settings/page.tsx` | AI 模型设置页 — CRUD、测试连接、设为默认 |
| `/writing` | `writing/page.tsx` | 写作页（骨架，待 v0.3 完整实现） |

## 认证流程

```
用户访问 → layout.tsx → AuthGate 检查
  ├── 未登录 → 显示登录/注册表单
  └── 已登录 → 渲染子页面
```

AuthGate 通过 Cookie 中的 JWT 自动验证，无需手动传递 token。

## 约定

- 使用 App Router（非 Pages Router），文件即路由
- 页面组件默认服务端渲染（RSC），客户端交互用 `'use client'` 指令
- 数据获取通过 `hooks/` 中的自定义 Hook 调用 `lib/api.ts`
- 全局状态通过 `stores/` 中的 store 管理
