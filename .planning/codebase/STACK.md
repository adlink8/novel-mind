# Novel-Mind — 技术栈文档

## 语言和运行时

| 层 | 语言 / 运行时 | 版本 |
|---|---|---|
| Backend | Python | 3.12（cpython，pyc 文件确认） |
| Frontend | Node.js / Next.js | Next.js 16.3.0-canary.6 |
| Frontend | React | 19.2.7 |

---

## Backend 主要依赖（requirements.txt）

| 包 | 版本约束 | 用途 |
|---|---|---|
| fastapi | >=0.115 | Web 框架 |
| uvicorn[standard] | >=0.32 | ASGI 服务器 |
| sqlalchemy | >=2.0 | ORM（async 模式） |
| asyncpg | >=0.30 | PostgreSQL async 驱动 |
| alembic | >=1.14 | 数据库迁移 |
| psycopg2-binary | >=2.9 | PostgreSQL 同步驱动（备用） |
| pydantic | >=2.13 | 数据验证 / Schema |
| pydantic-settings | >=2.8 | 配置管理 |
| python-dotenv | >=1.0 | 环境变量加载 |
| python-multipart | >=0.0.18 | 文件上传支持 |
| chardet | >=5.2 | 文件编码检测 |
| litellm | >=1.83.10 | AI 多 provider 路由 |
| pgvector | >=0.3 | PostgreSQL 向量扩展 Python 绑定 |
| chromadb | >=0.4.0 | 向量数据库客户端 |
| httpx | >=0.28 | 异步 HTTP 客户端 |
| cryptography | >=42.0 | 加密基础库 |
| passlib[bcrypt] | >=1.7 | 密码哈希 |
| bcrypt | >=4.0,<5.0 | bcrypt 实现 |
| pyjwt | >=2.8 | JWT 鉴权 |

---

## Frontend 主要依赖（package.json）

| 包 | 版本 | 用途 |
|---|---|---|
| next | 16.3.0-canary.6 | 全栈框架 |
| react / react-dom | 19.2.7 | UI 渲染 |
| typescript | devDependency | 类型系统 |
| @tanstack/react-query | ^5.50.0 | 服务端状态管理 |
| zustand | ^4.5.0 | 客户端状态管理 |
| axios | ^1.7.0 | HTTP 请求 |
| echarts + echarts-for-react | ^5.5.0 / ^3.0.2 | 可视化图表（知识图谱等） |
| @base-ui/react | ^1.5.0 | 无头 UI 组件库 |
| shadcn | ^4.10.0 | UI 组件（Radix 体系） |
| lucide-react | ^0.400.0 | 图标库 |
| tailwind-merge / tw-animate-css | ^2.6.1 / ^1.4.0 | Tailwind CSS 工具 |
| class-variance-authority | ^0.7.1 | 样式变体管理 |

---

## 数据库

| 数据库 | 版本 / 扩展 | 用途 |
|---|---|---|
| PostgreSQL | 16 + pgvector 扩展 | 主数据库：小说、章节、知识单元 |
| ChromaDB | >=0.4.0 | 向量数据库：文本 embedding 索引与语义搜索 |

---

## 向量模型

- 模型：nomic-embed-text
- 维度：768 维
- 用途：文本块 embedding，供 ChromaDB 语义搜索使用

---

## AI 路由

- 库：LiteLLM（>=1.83.10）
- 路由策略（ai_router.py）：
  - `quality`：高质量模型，用于复杂推理任务
  - `balanced`：均衡模型，日常生成任务
  - `budget`：低成本模型，批量处理任务
- 支持多 provider（OpenAI、Anthropic、本地模型等）

---

## 容器化

- 工具：Docker + docker-compose
- ChromaDB 以独立容器运行，宿主机端口映射至 8001
- Backend 以 uvicorn 容器运行
- Frontend 以 Next.js 容器运行
