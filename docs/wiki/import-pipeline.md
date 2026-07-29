# 导入管线

## 整体流程

```
上传 TXT (.txt)
    │
    ▼
创建 ImportJob（状态机：pending → uploading → detecting → parsing → saving → embedding → ready / failed）
    │
    ├── 编码检测（_decode_with_fallback）
    │   ├─ BOM 检测：\xff\xfe → UTF-16, \xfe\xff → UTF-16 BE, \xef\xbb\xbf → UTF-8-SIG
    │   ├─ chardet 检测（conf ≥ 0.55 才采用）
    │   └─ 候选列表试解码（按优先级打分）：
    │       utf-8 → gb18030 → gbk → big5 → shift_jis → euc-jp
    │       └─ 评分权重：CJK 比率 +10，可打印字符比率 +0.5，替换字符 -8
    │       └─ 低于 2% CJK（文本 >20K 字符）→ 报编码错误
    │
    ├── 章节拆分（CHAPTER_PATTERNS）
    │   ├─ 匹配顺序：
    │   │   第一章 / 第壹章 / 第一百二十章
    │   │   → 宽松中文数字（可能无「第」前缀）
    │   │   → Chapter 1（英文，大小写不敏感）
    │   │   → 1. xxx / 1、xxx（纯数字前缀）
    │   ├─ 有匹配：按标题切开，首标题前文本 >100 字作第 0 章「前言」
    │   ├─ 无匹配：固定 12,000 字一段硬切
    │   └─ 超大章节（>24,000 字）递归再分
    │
    ├── 写入数据库
    │   ├─ Novel 一行（title, author, chapter_count, word_count, status="ready"）
    │   └─ Chapter 一行/章（chapter_number, title, content 全文, word_count）
    │
    └── 建检索索引（异步，indexing_service.index_novel）
        ├── chunking_service.chunk_novel()
        │   每章切成 300-500 字语块，识别块类型：
        │   scene / dialogue / description / narration / paragraph
        ├── ai_service.embedding()
        │   每块调 LLM 算向量（配置的路由模型）
        ├── vector_store.add_chunks()
        │   向量存入 ChromaDB
        └── TextChunk.embedding_status = "embedded"
```

## 并发与幂等控制

- **Lease 机制**：每 5 分钟租约续期，防止重复处理
- **Content Hash**：SHA-256 文件内容哈希，同一文件重新上传只返回已有 job_id
- **重试恢复**：embedding/Chroma 短暂失败自动有限重试；服务重启时 `recover_stale_jobs()` 自动恢复超时任务。最终索引失败会进入 `indexing_failed`，不能伪装为 `ready`。
- **取消支持**：`cancel_job()` 终止运行中的导入

## 关键代码位置

| 模块 | 文件 |
|---|---|
| API 路由 | `backend/app/api/novels.py` |
| 导入状态机 | `backend/app/services/import_service.py` |
| 编码检测 + 分章 | `backend/app/services/novel_service.py` |
| 分块服务 | `backend/app/services/chunking_service.py` |
| 索引管线 | `backend/app/services/indexing_service.py` |
| 向量存储 | `backend/app/services/vector_store.py` |

---

> **常见追问**：导入是嵌入完成后才显示 ready？嵌入失败怎么办？→ [FAQ](faq.md#导入时已经进向量库了吗)
