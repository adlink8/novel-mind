# frontend/src/lib — 工具与 API 客户端层

共享工具函数和后端 API 封装，被页面和 hooks 引用。

## 文件

| 文件 | 职责 |
|---|---|
| `api.ts` (约 8KB) | Axios API 客户端 — 携带 Cookie 凭据，封装认证、小说 CRUD、AI 模型和混合搜索；同时导出底层 `api` 实例供评测页面使用 |
| `api.test.ts` (4.1KB) | API 客户端单元测试 |
| `utils.ts` (0.8KB) | 通用工具函数 — 日期格式化、文本截断、类名合并（`cn()`） |
| `utils.test.ts` (0.9KB) | 工具函数测试 |

## API 客户端使用

```typescript
import { novelsApi, searchApi } from '@/lib/api';

// 获取小说列表
const novels = await novelsApi.list();

// 上传小说
const result = await novelsApi.upload(file);

// 语义搜索
const results = await searchApi.inNovel(novelId, query);
```

所有请求自动携带认证 Cookie，401 时由 AuthGate 拦截处理。

## 约定

- 按业务域导出 `authApi`、`novelsApi`、`aiModelsApi`、`searchApi`
- 不直接使用 `axios`，统一通过 `api.ts` 导出的实例调用
- 错误处理在调用层进行，API 客户端不吞异常
