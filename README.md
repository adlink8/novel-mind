# NovelMind

AI 辅助小说创作与理解平台 —— 让 AI 成为你的小说伙伴。

## 功能

- **AI 剧情理解**：导入小说，AI 自动分析剧情、人物、伏笔、叙事结构
- **AI 续写/同人文**：基于原作风格续写同人小说
- **时间线可视化**：智能提取并可视化小说事件时间线
- **人物关系图谱**：自动构建可交互的人物关系网络

## 技术栈

- **前端**：Next.js 14 + TypeScript + Tailwind CSS + shadcn/ui
- **后端**：Python FastAPI + SQLAlchemy
- **数据库**：PostgreSQL + pgvector
- **AI**：LiteLLM（支持 OpenAI / Claude / Ollama 等多模型）
- **可视化**：ECharts

## 快速开始

### 前置要求

- Docker & Docker Compose
- Node.js 20+（本地开发）
- Python 3.12+（本地开发）

### Docker 一键启动

```bash
# 1. 配置环境变量
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入你的 AI 模型 API Key

# 2. 启动所有服务
docker compose up -d

# 3. 访问
# 前端：http://localhost:3000
# 后端 API：http://localhost:8000/docs
```

### 本地开发

```bash
# 后端
cd backend
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload

# 前端
cd frontend
npm install
npm run dev
```

## 项目结构

```
novel-mind/
├── frontend/           # Next.js 前端
│   ├── src/
│   │   ├── app/        # 页面路由
│   │   ├── components/ # UI 组件
│   │   ├── lib/        # 工具函数 & API 客户端
│   │   ├── hooks/      # 自定义 Hooks
│   │   ├── stores/     # Zustand 状态管理
│   │   └── types/      # TypeScript 类型
│   └── public/         # 静态资源
├── backend/            # FastAPI 后端
│   ├── app/
│   │   ├── api/        # API 路由
│   │   ├── services/   # 业务逻辑
│   │   ├── models/     # 数据库模型
│   │   ├── schemas/    # Pydantic 校验
│   │   └── core/       # 核心工具
│   └── tests/
├── docs/               # 项目文档
│   ├── 需求文档.md
│   ├── 技术架构.md
│   ├── 路线图.md
│   ├── 项目状态.md
│   └── 待办清单.md
└── docker-compose.yml
```

## 支持的 AI 模型

| 提供商 | 模型示例 | 配置方式 |
|--------|---------|---------|
| OpenAI | GPT-4o, GPT-4o-mini | API Key |
| Anthropic | Claude 3.5 Sonnet | API Key |
| Ollama | Qwen2, Llama3 | 本地地址 |
| 自定义 | 兼容 OpenAI API 的服务 | Base URL + Key |

## 路线图

详见 [docs/路线图.md](docs/路线图.md)，共 7 个阶段，预计 4 个月完成 MVP。

## License

MIT
