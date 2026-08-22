# Desktop 数据生命周期（Phase 43）

本文档描述 NovelMind Windows Desktop 的数据布局、版本化迁移与失败恢复契约（D-43-05 / D-43-06 / D-43-08）。实现代码在 `desktop/src/data/`，测试在 `desktop/tests/data/`。

## 布局（D-43-05）

所有可变数据、日志、迁移状态与备份都位于**单一版本化根目录**：

```
%APPDATA%/NovelMind/            ← Electron app.getPath('userData')
├── data/                       ← 可变用户数据（迁移后的内容、uploads、storage）
├── logs/                       ← 有界、轮转的诊断日志（43-02 拥有）
├── backups/                    ← hash 背书、有界保留的迁移/恢复证据
├── runtime/                    ← 运行时账本（迁移 journal）
├── secrets/                    ← 凭据/密钥；本层绝不迁移、绝不备份
└── migration.json              ← 版本元数据：layout/schema/runtime 版本 + txnId
```

- 安装目录（`installRoot`）是**只读输入**：布局构造器在任意路径派生前拒绝 app-data 与安装目录的重叠（双向）。
- 每个可变写入路径都通过 `containPath` 派生：规范化绝对路径、拒绝 traversal、拒绝绝对路径段、拒绝写入安装根。
- 首次启动与兼容升级均可重复初始化（幂等）。重复初始化只确保目录存在，不产生副作用。

### 版本状态（migration.json）

| 字段 | 含义 |
|---|---|
| `layoutVersion` | 目录/布局契约版本（`APP_DATA_LAYOUT_VERSION`） |
| `schemaVersion` | 数据 schema 版本（迁移目标/当前值） |
| `runtimeVersion` | 应用运行版本字符串 |
| `committedAt` / `txnId` | 最近一次原子提交的时间与迁移事务 id |

`migration.json` 以**原子写**（tmp 文件 + rename）提交；写失败时旧版本文件保持不变。缺失或损坏的元数据读作"未初始化"（schemaVersion 0），绝不视为崩溃。

## 备份优先迁移（D-43-06）

迁移是一个可恢复事务，由 `MigrationRunner` 执行：

1. **BACKUP** — 在迁移任何数据之前，先对 `data/` 做 hash 背书快照（每个文件记录 sha256 + size，写入 `backups/<txnId>/manifest.json`）。磁盘空间不足时在写入任何字节前**显式失败**（`INSUFFICIENT_SPACE`）。保留有界（默认保留最新 5 份）。
2. **MIGRATE** — 声明步骤按固定顺序运行：`files → database → vector → app_metadata`。步骤由外部注入（运行时不是数据库权威，D-43-04）。内置的 `files` 步骤把只读资源树复制进 `data/` 并逐文件 re-hash 校验。
3. **COMMIT** — 新版本状态原子提交。只有提交完成后 `needsMigration()` 才返回 false，运行时才能进入 `ready`。
4. **FAILURE** — 旧数据永不被删除或原地覆盖；备份保留，失败以 `MigrationFailure`（typed code + 有限恢复指令）返回，`oldDataPreserved` 恒为 true。

重试幂等：`runtime/migration-journal.json` 记录进行中的 txn、已完成的步骤与备份目录。重试时先对备份做 hash 校验，校验通过则复用证据并从中断步骤继续（不重新备份、不重复已完成步骤）；备份损坏则 typed 失败（`BACKUP_FAILED`），绝不静默重建。

## 失败与恢复

| 故障 | 结果 | 用户动作 |
|---|---|---|
| 磁盘空间不足 | `INSUFFICIENT_SPACE`，未写任何字节 | 释放磁盘后重试 |
| app-data 写入被拒 | `BACKUP_FAILED`，旧数据完好 | 检查目录权限后重试 |
| 迁移步骤抛错 | `STEP_FAILED`，备份+journal 保留 | 修复原因后重试（从中断步骤继续） |
| 备份 hash 不匹配 | `BACKUP_FAILED`，拒绝复用 | 停止，从更早备份恢复或联系支持 |
| 版本提交失败 | `COMMIT_FAILED`，旧数据完好 | 重试，或从备份恢复 |

运行时接线（`migrationGateFrom`）：迁移门被注入 `DesktopRuntime`，`migrating` 状态消费 `needsMigration()/run()`。迁移失败 → 运行时 `failed`（`MIGRATION_FAILED`），**绝不从部分迁移上报 ready**。

## 数据源与迁移顺序

首次运行/升级时按声明顺序迁移：

1. 文件：`backend/uploads/`（小说文件，复制）→ `data/uploads/`
2. 文件：`backend/storage/`（插图资产生成物，复制）→ `data/storage/`
3. 数据库：PostgreSQL docker volume（pg_dump）→ 数据层
4. 向量：Chroma volume（SQLite 目录复制）→ 数据层
5. 应用元数据

`backend/evals/` 为只读评估数据，不迁移。后端 `storage_dir` 配置项（`NOVELMIND_STORAGE_DIR`）让打包模式的 storage 重定向到 app-data，而非 CWD 兜底。

## 目录所有权

| 目录 | 所有者 | 说明 |
|---|---|---|
| `data/` | 桌面数据层（本计划） | 迁移/恢复的写入目标 |
| `logs/` | 43-02 运行时日志 | 有界、重定向、轮转 |
| `backups/` | 本计划 | 迁移证据，有界保留 |
| `runtime/` | 本计划 + 43-02 | migration journal、运行时快照 |
| `secrets/` | 43-04+ 凭据交付 | 本层不迁移、不备份 |

## 相关代码

- `desktop/src/data/app-data-layout.ts` — 路径权威、`DataFs` 注入缝、初始化
- `desktop/src/data/version-state.ts` — 版本元数据原子读写
- `desktop/src/data/backup.ts` — manifest/hash 备份、校验、恢复、保留
- `desktop/src/data/migration-runner.ts` — 备份优先迁移事务 + runtime 门适配
- `desktop/tests/data/app-data-layout.test.ts`、`migration-recovery.test.ts` — 布局与故障注入套件
