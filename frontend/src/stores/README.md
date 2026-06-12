# frontend/src/stores — 状态管理层

基于 Zustand 的全局状态管理，实现跨组件的共享状态。

## Store 列表

| Store | 文件 | 职责 |
|---|---|---|
| `aiConfigStore` | `aiConfigStore.ts` (4.3KB) | AI 模型配置全局状态 — 模型列表、默认模型、加载状态、CRUD 操作 |
| `novelStore` | `novelStore.ts` (1.9KB) | 小说列表全局状态 — 小说集合、当前选中、加载状态 |

## 使用示例

```tsx
import { useNovelStore } from '@/stores/novelStore';

function MyComponent() {
  const novels = useNovelStore((s) => s.novels);
  const fetchNovels = useNovelStore((s) => s.fetchNovels);
  // ...
}
```

## 约定

- 使用 Zustand 的 `create()` API，不引入额外中间件（无特殊需求时）
- Store 内包含数据和操作方法（fetch / add / remove / update）
- 异步操作在 store 内部调用 `lib/api.ts`，状态变更通过 `set()` 同步
- 不需要跨组件共享的状态保持在组件内部（useState / useReducer）
