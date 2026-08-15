# 04 — 请求与业务流

描述 NovelMind 主要请求链路的完整流程，包括认证、小说导入、阅读、读者问答（Skill Run 运行时）、RAG 检索等。

## 认证流程

### 注册

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as Next.js 前端
    participant B as FastAPI (/api/auth)
    participant DB as PostgreSQL

    U->>F: 填写用户名/邮箱/密码
    F->>B: POST /api/auth/register
    B->>B: 校验密码长度 (<=72 bytes)
    B->>B: bcrypt 哈希
    B->>DB: INSERT INTO users
    DB-->>B: user_id
    B->>B: 签发 JWT
    B-->>F: Set-Cookie: access_token
    B-->>F: {user, access_token}
    F->>F: 存储认证状态，跳转首页
```

### 登录

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as Next.js 前端
    participant B as FastAPI (/api/auth)
    participant DB as PostgreSQL

    U->>F: 输入用户名/密码
    F->>B: POST /api/auth/login
    B->>DB: SELECT by username
    DB-->>B: hashed_password
    B->>B: bcrypt.verify
    B->>B: 签发 JWT (iss/aud exp)
    B-->>F: Set-Cookie: access_token (HttpOnly SameSite)
    B-->>F: {access_token, token_type}
```

### 认证中间件

所有业务 API 请求（除 health/register/login/logout）都经过以下认证链：

```
HTTP 请求
  → core.security.get_current_user / require_user
    → 从 Cookie 中提取 access_token
    → 若 Cookie 不存在，从 Authorization: Bearer 提取
    → JWT 验证（签名、iss/aud、过期时间）
    → 查询数据库确认用户存在且 is_active
    → 注入 current_user 到路由处理函数
  
  写请求额外校验：Origin 头必须在 CORS 白名单中
```

**来源**:
- `backend/app/api/dependencies.py` — `require_owned_novel`
- `backend/app/core/security.py` — JWT 签发/验证
- `backend/app/api/auth.py` — 注册/登录/注销端点

---

## 小说导入流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as Next.js
    participant B as FastAPI (/api/novels)
    participant S as novel_service
    participant FS as 文件系统
    participant DB as PostgreSQL

    U->>F: 选择 TXT 文件并上传
    F->>B: POST /api/novels/upload (multipart/form-data)
    B->>B: 认证 + owner 提取
    B->>S: create_import_job + BackgroundTasks.process_import

    S->>S: 安全校验（文件类型 .txt、大小限制）
    S->>FS: 生成随机文件名，分块写入
    S->>S: 多编码检测（UTF-8/GB18030/Big5/Shift_JIS）
    S->>S: 文本清洗
    S->>S: 章节分割（正则匹配"第X章"等）
    S->>DB: BEGIN TRANSACTION
    S->>DB: INSERT INTO novels
    S->>DB: INSERT INTO chapters (批量)
    S->>DB: INSERT INTO import_jobs
    S->>DB: COMMIT

    alt 事务失败
        S->>FS: 删除已写入文件（补偿）
        S-->>B: 500 错误
    end

    B-->>F: 200 {job_id, status=pending}
    F->>B: 轮询 import-status
    F->>U: 显示阶段进度与最终结果
```

**来源**:
- `backend/app/services/novel_service.py` — 进口核心逻辑
- `backend/app/services/import_service.py` — 任务状态管理
- `backend/app/api/novels.py` — 上传端点

---

## 小说阅读流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as Next.js 阅读器
    participant B as FastAPI (/api/novels)
    participant DB as PostgreSQL

    U->>F: 点击小说
    F->>B: GET /api/novels/{id}
    B->>B: 校验 owner_id == current_user.id
    B->>DB: SELECT novel (不含 source_path)
    DB-->>B: {id, title, author, chapter_count, word_count, ...}
    B-->>F: NovelResponse

    U->>F: 点击章节
    F->>B: GET /api/novels/{id}/chapters/{chapter_id}
    B->>B: 校验 novel.owner_id == current_user.id
    B->>DB: SELECT chapter
    DB-->>B: {id, chapter_number, title, content, word_count}
    B-->>F: ChapterResponse
    F->>U: 渲染章节内容

    Note: 阅读进度暂存于 Novel.reading_progress 字段
```

---

## 读者问答流程（嵌入式 Novel Agent 运行时）

阅读器选中文字提问后，FastAPI 侧只组装并冻结上下文、入队 SkillRun；模型执行由 agent-service 的 queued-run poller **拉模式**完成（绝不 FastAPI→agent-service）。

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as Next.js 阅读器
    participant B as FastAPI (/api/reader-chat)
    participant DB as PostgreSQL
    participant AG as agent-service (poller)
    participant GW as 模型网关

    U->>F: 选中文字 → 问 AI
    F->>B: POST /api/reader-chat/{novel_id}/conversations/{id}/ask
    B->>B: validate_selection + 检索证据(剧透过筛) + 冻结 ContextManifest
    B->>DB: INSERT ReaderGenerationJob + SkillRun(queued, origin='reader_chat')
    B-->>F: 任务受理（前端轮询消息列表）

    alt 证据维度不足（chat_backfill）
        B->>DB: INSERT SkillRun(queued, origin='chat_backfill')（≤2 个，按维度映射去重）
        B->>DB: job 挂起 paused_dependency: waiting_analysis:<维度>
        Note over B,DB: 无映射维度 → 诚实失败 backfill_unavailable
    end

    AG->>B: GET /api/agent/queued-runs（定时轮询）
    B-->>AG: queued runs
    AG->>B: POST .../claim（原子 claim + lease 过期 reclaim）

    alt agentic 模式（answer-reading-question 等）
        AG->>B: 域工具调用（search_novel_text / get_evidence_span，owner/cutoff/预算门照常生效）
        AG->>GW: pi 工具循环，模型自编排工具并输出结构化 JSON
        Note over AG: 运行中预算熔断：max_calls 超限立即 abort
    else guided 模式（build-visual-bible / detect-key-scenes）
        AG->>B: 程序确定性检索（bigram 扇出）+ chunk_id 物化
        AG->>GW: 单次网关补全（编号摘录菜单 → 语义 JSON，菜单绝不含 evidence_key）
        AG->>AG: evidence_indices → evidence_key 翻译（越界 fail closed）
    end

    Note over AG: 模型只产语义字段；schema/policy/manifest/claim 哈希与<br/>snapshot/cutoff 血缘由投影层程序注入
    AG->>B: POST finalize（envelope + frozen_manifest + usage）
    B->>B: integrity 门 + 预算 fail-closed + 引用白名单校验
    alt 校验通过
        B->>DB: candidate-only Artifact + 域表 candidate 行<br/>（或 reader_chat 答案物化回 ReaderMessage/Citation）
        Note over DB: backfill 物化完成(materialized:...) → reconcile 重建 manifest → 重入队 answer run
    else 校验失败 / 取消
        B->>DB: run failed/cancelled，零写入
    end
```

**关键约束**：

- 修复环有界（MAX_REPAIR_ROUNDS=2）：信封构建/校验失败把错误清单 + 已物化证据菜单喂回同一会话定向修正，超出 fail closed
- 产物一律 candidate-only（如 visual_bible_versions、key_scene_sets 的 candidate 行）；只有用户在审查界面显式批准（append-only review 事件）后才成为可用权威
- `waiting_analysis` 挂起有 30 分钟超时与恢复分类：backfill failed/cancelled/超时/物化失败均让 job 诚实失败，不永久停摆

**来源**:
- `backend/app/services/agent_runtime/reader_bridge.py` — reader chat ↔ SkillRun 桥接（入队/挂起/reconcile/答案物化）
- `backend/app/services/agent_runtime/backfill.py` — `DIMENSION_TO_SKILL` 维度映射 + backfill run 创建（快照锚定按命名空间）
- `backend/app/services/agent_runtime/finalize.py` — 确定性 finalizer（integrity 门、零写入纪律）
- `agent-service/src/poller.ts` — 轮询/claim/agentic 执行/修复环/预算熔断
- `agent-service/src/guided/executor.ts` — guided 模式编排（GUIDED_SKILLS 注册表、单次补全）
- `agent-service/src/guided/retrieval.ts` — bigram 扇出检索 + chunk_id 物化 + 编号菜单
- `agent-service/src/guided/translate.ts` — evidence_indices → evidence_key（fail closed）
- `agent-service/src/structured-output/*-projection.ts` — 语义→契约投影（哈希/血缘程序注入）

---

## RAG 语义搜索流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as Next.js
    participant B as FastAPI (/api/rag)
    participant IDX as indexing_service
    participant CHK as chunking_service
    participant AI as ai_service (embedding)
    participant VS as ChromaDB

    alt 触发索引
        U->>F: 在小说详情点击"建立索引"
        F->>B: POST /api/rag/index/{novel_id}
        B->>B: 校验 owner
        B->>IDX: index_novel(novel_id)
        IDX->>CHK: chunk(novel)
        CHK-->>IDX: List[TextChunk]
        IDX->>AI: embed(chunks)
        AI-->>IDX: List[vectors]
        IDX->>VS: add(collection=novel_{id}, vectors, metadata)
        IDX->>DB: UPDATE text_chunks.embedding_status = 'embedded'
        B-->>F: {status: "completed", chunk_count}
    end

    alt 语义搜索
        U->>F: 输入搜索查询
        F->>B: POST /api/rag/search {query, novel_id, top_k}
        B->>AI: embed(query)
        AI-->>B: query_vector
        B->>VS: search(collection=novel_{id}, query_vector, k=top_k)
        VS-->>B: [chunks + scores]
        B-->>F: SearchResponse {results: [{chunk, score, chapter_info}]}
        F->>U: 展示搜索结果
    end
```

## RAG 评测边界

`/api/eval` 全部强制认证，并通过 `EvalDataset/EvalRun -> Novel.owner_id` 进行资源隔离。API 层负责权限与输入校验，`eval_service` 只负责策略执行和指标持久化。当前 `POST /api/eval/runs` 会同步等待整次评测完成，长任务后台化仍是待办。

**来源**:
- `backend/app/api/rag.py` — RAG 端点
- `backend/app/services/chunking_service.py` — 分块
- `backend/app/services/indexing_service.py` — 索引管线
- `backend/app/services/vector_store.py` — ChromaDB 封装
- `backend/app/services/ai_service.py` — embedding 调用

---

## AI 模型配置流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as Next.js 设置页
    participant B as FastAPI (/api/models)
    participant CR as crypto (Fernet)
    participant DB as PostgreSQL

    U->>F: 添加 AI 模型
    F->>B: POST /api/models {name, provider, api_key, ...}
    B->>B: 校验 owner
    B->>CR: encrypt(api_key)
    CR-->>B: "enc:v1:..." 密文
    B->>DB: INSERT INTO ai_model_configs
    DB-->>B: model_id
    B-->>F: AIModelConfigResponse (不含 api_key)

    Note: 读取时自动解密。API key 在 Response 中永远不返回
```

---

## 请求安全防御链

每个非公开 API 请求经过以下防御层：

```
1. CORS 中间件 → 校验 Origin 头
2. TrailingSlash 中间件 → 规范化 URL
3. RequestLogging 中间件 → 结构化日志（脱敏）
4. 认证依赖 → get_current_user → JWT 验证
5. Owner 依赖 → 权限校验 → 404 或 403
6. SSRF 校验 → url_security（仅 provider URL 调用时）
7. 输入校验 → Pydantic schema 验证
8. 响应序列化 → 脱敏（去掉 source_path、api_key 等）
```
