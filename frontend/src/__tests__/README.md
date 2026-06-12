# frontend/src/__tests__ — 前端测试

Vitest + React Testing Library 测试套件，覆盖 API 客户端和工具函数。

## 统计

- **测试数量**: 22 个，全部通过
- **框架**: Vitest + React Testing Library
- **环境**: jsdom（模拟浏览器环境）

## 运行

```bash
cd frontend

# 全部测试
npm test

# 监视模式
npm run test:watch

# 带覆盖率
npm test -- --coverage
```

## 覆盖范围

| 测试文件 | 覆盖内容 |
|---|---|
| `lib/api.test.ts` | API 客户端 — 请求参数、响应处理、错误状态 |
| `lib/utils.test.ts` | 工具函数 — `cn()` 类名合并、日期格式化 |
| `stores/*` | Zustand store — 状态更新、异步操作 |
| `hooks/*` | 自定义 Hook — 数据获取、加载/错误状态 |
| `components/*` | 组件渲染 — 基础 UI 行为 |

## CI

通过 GitHub Actions 自动运行（`.github/workflows/frontend-tests.yml`），PR 门禁。

## 配置

- `vitest.config.ts` — Vitest 配置，包含路径别名（`@/` → `src/`）
- `setup.ts` — 测试环境初始化（jsdom、全局 mock）
