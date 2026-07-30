# frontend/src/hooks — 自定义 Hooks

React 自定义 Hook，封装数据获取和副作用逻辑，供页面组件使用。

## Hook 列表

| Hook | 文件 | 职责 |
|---|---|---|
| `useNovels` | `use-novels.ts` (2.6KB) | 小说数据获取 — 列表加载、刷新、分页 |
| `useAiModels` | `use-ai-models.ts` (2.1KB) | AI 模型数据获取 — 配置列表、CRUD、测试连接 |

## 使用示例

```tsx
import { useNovels } from '@/hooks/use-novels';

function NovelsPage() {
  const { novels, isLoading, error, refresh } = useNovels();
  // ...
}
```

## 约定

- 遵循 `useXxx` 命名规范（React Hook 规则）
- Hook 内部使用 `useEffect` + `useState` 管理异步数据获取
- 与 `stores/` 的分工：Hook 负责触发操作和暴露状态给单个组件，Store 负责跨组件共享状态
- 避免在 Hook 中进行复杂业务逻辑处理，这些属于 Service 层（后端）或 Store（前端全局）
