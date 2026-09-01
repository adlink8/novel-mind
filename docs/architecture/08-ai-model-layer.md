# 08 — AI 模型配置与统一调用层 (AI Provider & Unified Protocol Layer)

AI 模型的配置管理、密钥安全加密、五大多模态提供商归一化适配、运行时动态发现与调用路由。

---

## 架构概览

```
用户配置 / 动态发现 AI 模型 (/api/models/discover)
  → AIModelConfig ORM 存储（API Key Fernet 加密）
  → 五大统一 Provider 适配器 (OpenAI / Anthropic / Ollama / DeepSeek / OpenCode)
  → ai_router 按任务类型和层级选择最优模型
  → ai_service / Pi Gateway 统一执行与流式输出
  → AIUsageLog 记录调用成本与延迟
```

---

## AI 模型配置与五大 Provider 协议

### 1. AIModelConfig 实体模型

**来源**: `backend/app/models/ai_model.py`

| 字段 | 类型 | 说明 |
|---|---|---|
| `owner_id` | int (FK → users.id) | 所有者隔离（CASCADE），同用户下名称唯一 |
| `name` | str | 用户自定义显示名称 |
| `provider` | str | **五大支持协议**: `openai` / `anthropic` / `ollama` / `deepseek` / `opencode` |
| `model_id` | str | 模型唯一标识，如 `gpt-4o`, `claude-3-5-sonnet`, `qwen2.5:7b`, `deepseek-chat` |
| `api_key` | str | Fernet 加密密文（`enc:v1` 前缀），写入加密，读取自动解密 |
| `base_url` | str? | 自定义 API 地址（受 SSRF 白名单与 IP 过滤安全约束） |
| `tier` | str | `quality` / `balanced` / `budget` |
| `is_default` | bool | 是否用户默认模型 |

---

### 2. API 端点与动态网关发现 (Phase 46)

**来源**: `backend/app/api/models.py`

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/api/models` | 当前用户的模型配置列表 |
| `GET` | `/api/models/discover` | **动态网关发现**：SSRF 安全扫描并发现局域网或云端可用模型列表 |
| `POST` | `/api/models` | 添加模型配置 |
| `PUT` | `/api/models/{id}` | 更新配置 |
| `DELETE` | `/api/models/{id}` | 删除配置 |
| `POST` | `/api/models/{id}/test` | 测试模型连接联通性与延迟 |
| `POST` | `/api/models/{id}/set-default` | 设为默认模型 |

---

### 3. 提供商归一化与清理说明 (Phase 46)

* **五大统一 Provider 矩阵**:
  1. **OpenAI**: GPT-4o / GPT-4o-mini / Text-Embedding-3
  2. **Anthropic**: Claude 3.5 Sonnet / Claude 3.5 Haiku
  3. **Ollama**: 本地私有化大模型与向量模型（`bge-m3`, `nomic-embed-text`, `qwen2.5`）
  4. **DeepSeek**: DeepSeek-V3 / DeepSeek-R1 推理与对话
  5. **OpenCode / Custom**: 兼容 OpenAI 协议的自建大模型网关与私有部署
* **Vertex AI 清理记录**: 历史 Vertex AI 原生适配器、GCP 凭据依赖与非标接口已在 Phase 46 全部物理移除，实现纯净轻量化统一调用契约。

---

## AI 调用路由与任务分级

### ai_router 模块

**来源**: `backend/app/services/ai_router.py`

| 任务类型 | 优选 Tier | 推荐模型与选型逻辑 |
|---|---|---|
| **RAG Embedding** | `budget` | 向量计算吞吐量大，优先选用 `nomic-embed-text` / `bge-m3` / `text-embedding-3-small` |
| **Analysis / Chat** | `quality` | 深度阅读理解与结构化生成，优选 `gpt-4o` / `claude-3-5-sonnet` |
| **Knowledge Extraction** | `quality` | 实体关系判定与双向逻辑裁决，要求极低幻觉率 |
| **Fanfiction / Creative** | `balanced` | 同人创作与续写，平衡推理成本与文学文采 |
| **Summary / Outline** | `balanced` | 章节大纲与宏观摘要生成 |
