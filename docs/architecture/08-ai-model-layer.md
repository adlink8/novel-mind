# 08 — AI 模型配置与调用层

AI 模型的配置管理、密钥加密、调用路由与成本统计。

## 架构概览

```
用户配置 AI 模型
  → AIModelConfig ORM 存储（API Key Fernet 加密）
  → ai_router 按任务类型和层级选择最优模型
  → ai_service (LiteLLM) 统一调用外部 AI API
  → AIUsageLog 记录调用成本和延迟
```

## AI 模型配置

### AIModelConfig 模型

**来源**: `backend/app/models/ai_model.py`

| 字段 | 说明 |
|---|---|
| `owner_id` | 所有者（FK → users.id CASCADE），同用户下名称唯一 |
| `name` | 用户自定义显示名称 |
| `provider` | openai / anthropic / ollama / custom |
| `model_id` | 如 gpt-4o、claude-3.5-sonnet、qwen2:7b |
| `api_key` | Fernet 加密密文（`enc:v1` 前缀），读取时自动解密 |
| `base_url` | 自定义 API 地址 |
| `tier` | quality / balanced / budget |
| `is_default` | 是否默认模型 |

### API 端点

**来源**: `backend/app/api/models.py`

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/api/models` | 当前用户的模型列表 |
| `POST` | `/api/models` | 添加模型配置 |
| `PUT` | `/api/models/{id}` | 更新配置 |
| `DELETE` | `/api/models/{id}` | 删除配置 |
| `POST` | `/api/models/{id}/test` | 测试模型连接 |
| `POST` | `/api/models/{id}/set-default` | 设为默认模型 |

### API Key 加密

参见 [07 认证与安全架构](07-auth-security.md#api-key-加密)。

### 默认模型选择

```
用户级默认: is_default=True 的 AIModelConfig
系统回退: 无默认时拒绝调用（不暴露系统级 key）
```

## AI 调用路由

### ai_router 模块

**来源**: `backend/app/services/ai_router.py`（8.0KB）

按任务类型和层级选择模型：

| 任务类型 | 优选 tier | 说明 |
|---|---|---|
| chunking → embedding | budget | embedding 任务量最大，优先低成本 |
| analysis → chat | quality | 分析质量重要，优先高能力模型 |
| fanfiction → chat | balanced | 创作平衡成本与质量 |
| summary → chat | balanced | 摘要中等复杂度 |

### ai_service 模块

**来源**: `backend/app/services/ai_service.py`（4.9KB）

LiteLLM 统一封装：

```python
# Chat 调用
response = await ai_service.chat(
    messages=[{"role": "user", "content": "..."}],
    model=selected_model,
    max_tokens=4096,
    temperature=0.7
)

# Embedding 调用
vectors = await ai_service.embed(
    texts=["文本1", "文本2"],
    model=embedding_model
)

# 流式调用
async for chunk in ai_service.stream_chat(messages, model):
    yield chunk
```

支持所有 OpenAI-compatible 提供商（OpenAI、Anthropic、Ollama、自定义端点等）。

### SSRF 双重校验

自定义 `base_url` 在配置时和每次 API 调用前执行 SSRF 校验：

```python
# 配置时
url_security.validate_url(base_url, allowlist)

# 调用前
url_security.validate_url(resolved_url, allowlist)
```

## 成本统计

### AIUsageLog 模型

**来源**: `backend/app/models/ai_usage_log.py`

每次 API 调用自动记录：

| 字段 | 说明 |
|---|---|
| `model_name` | 实际使用的模型 |
| `task_type` | analysis / embedding / fanfiction / summary / timeline |
| `input_tokens` | 输入 token 数 |
| `output_tokens` | 输出 token 数 |
| `cost_usd` | 费用（美元） |
| `latency_ms` | 响应延迟 |
| `status` | success / failed / timeout |
| `novel_id` | 关联小说（逻辑关联，无 FK 约束） |

## 当前状态与未来

### VERIFIED

- 模型配置 CRUD + API Key 加密
- LiteLLM 统一调用封装（chat + embedding + stream）
- SSRF 防护（配置时 + 调用前双重校验）
- Owner 隔离

### PARTIAL

- AI 路由与成本统计：服务骨架存在，**业务生成端点仍未接入**（分析、创作路由返回 501）
- AIUsageLog 表存在，记录逻辑待接入业务端点

### PLANNED (Phase 3)

Phase 3 AI 分析与创作（剧情分析、人物抽取、时间线、同人续写）将复用本层：

```
ai_router 选模型 → ai_service 调用 → AIUsageLog 记录
```

无需修改 AI 模型配置层的核心逻辑。

## 修改后验证

```bash
cd backend
source venv/Scripts/activate
pytest tests/test_ai_model.py -v
```
