# NovelMind Desktop 升级与恢复（Phase 45）

本文档描述 NovelMind Windows Desktop 的版本升级策略、失败恢复与卸载策略
（D-45-04 / D-45-05 / T-45-02-01 / T-45-02-02 / T-45-02-03）。实现代码在
`desktop/src/update/`，测试在 `desktop/tests/update/`，升级夹具在
`desktop/test-fixtures/prior-version/`。

## 升级策略（D-45-04）

升级是**备份优先、可回滚**的事务，由 `UpgradeCoordinator`
（`desktop/src/update/upgrade-coordinator.ts`）在 43-03 `MigrationRunner` 之上编排：

1. **检测** — 比较已提交版本状态（schema + runtime 版本）与当前运行二进制。
   - 版本**回退**（旧二进制打开新数据）在任何写入前被**拒绝**（`VERSION_REGRESSION`）：
     数据与版本元数据均不被触碰。
   - 相同 schema、应用版本更新 → 仅记录运行版本（`metadata-only`，原子写）。
   - schema 落后于目标 → 完整备份优先迁移（`upgrade-needed`）。
2. **准备** — 先停止自有运行时（注入），再对**校验和钉死的前一版本夹具**做逐文件
   SHA-256 校验；不匹配/被篡改 → 拒绝（`FIXTURE_MISMATCH`），任何数据不被修改。
   备份容量是 `MigrationRunner` 的显式 `INSUFFICIENT_SPACE` 门（写入前失败）。
3. **迁移** — 声明步骤按固定顺序执行（`files → database → vector → app_metadata`），
   版本提交（原子 tmp+rename）只发生在全部步骤成功之后。
4. **校验** — 注入的升级后域探针重新验证新数据；探针失败 → 自动回滚，**绝不提交
   一个已验证失败的版本**（fail-closed）。
5. **回滚** — `rollback()` 校验并恢复升级前备份，把 `data/` 精确还原到升级前状态
   （含删除迁移新增文件、裁剪空目录），恢复源版本元数据并清空 journal——之后的重试
   从全新备份开始（幂等）。

重复调用幂等：已提交状态不低于目标 → 返回 `current`，不重复迁移、不重复备份。

## 失败恢复与回滚

| 故障 | 结果 | 用户动作 |
|---|---|---|
| 版本回退（旧二进制/新数据） | `VERSION_REGRESSION`，数据未修改 | 安装匹配或更新的版本 |
| 夹具校验失败（被篡改/缺文件） | `FIXTURE_MISMATCH`，数据未修改 | 恢复原始数据后重试 |
| 自有运行时无法停止 | `RUNTIME_STOP_FAILED`，数据未修改 | 关闭所有 NovelMind 实例后重试 |
| 磁盘空间不足 | `INSUFFICIENT_SPACE`，未写任何字节 | 释放磁盘后重试 |
| 迁移步骤失败 | `STEP_FAILED`，备份+journal 保留，typed 恢复指令 | 修复原因后重试（从已验证备份继续） |
| 备份校验失败（hash 不匹配） | `BACKUP_FAILED`，拒绝复用证据 | 停止，从更早备份恢复或联系支持 |
| 升级后探针失败 | `POST_UPGRADE_PROBE_FAILED`，已自动回滚 | 修复后重试升级 |
| 用户选择回滚 | `rollback()` 精确恢复旧数据+版本 | 成功后可重新升级 |

`migration-journal.json` 是重试游标：中断/失败的尝试记录 txn、停下的步骤与备份目录；
重试先对备份做 hash 校验，校验通过则复用证据从中断步骤继续。

## 卸载策略（D-45-05）

- **默认卸载只移除安装二进制，保留全部 `%APPDATA%/NovelMind` 用户数据**
  （`electron-builder.yml` 的 `deleteAppDataOnUninstall: false`，每用户安装）。卸载后
  数据仍在，重装后**哈希逐文件一致**，升级协调器视为 `current`，不会重新迁移。
- **删除数据是独立、明确标注的动作**（`desktop/src/update/uninstall-policy.ts`）：
  - 从不由默认卸载路径触发；
  - 需要显式确认（`confirm: true`）；
  - 目标经 `containPath`/`isPathInside` 严格限定在解析后的 app-data 根内：
    路径穿越、绝对越界路径 → typed `OUTSIDE_APP_DATA` 拒绝；
  - 删除失败 → typed `DELETE_FAILED`，不误报成功。

数据位置：

```
%APPDATA%/NovelMind/
├── data/       ← 用户内容（小说库、章节、分析、视觉、衍生）
├── logs/       ← 有界诊断日志
├── backups/    ← 迁移/回滚的 hash 背书备份（有界保留）
├── runtime/    ← 迁移 journal 等运行账本
├── secrets/    ← 凭据（本层不迁移、不备份）
└── migration.json
```

## 升级夹具（先于空库的证据）

`desktop/scripts/create-upgrade-fixture.ps1` 生成**校验和钉死的前一版本夹具**
（`desktop/test-fixtures/prior-version/`）：包含真实用户数据（library/chapters/
analysis/visuals/derivatives）、新版本不可变资源（templates/assets）与版本元数据
（schema 1 / runtime 0.1.0）。`fixture-manifest.json` 记录每个文件（data/、
resources/ 与 migration.json）的 sha256 + size。升级测试从该夹具起步，绝不从空库验证。

## 相关代码

- `desktop/src/update/upgrade-coordinator.ts` — 升级检测/迁移/校验/回滚
- `desktop/src/update/uninstall-policy.ts` — 默认卸载范围 + 显式删除策略
- `desktop/scripts/create-upgrade-fixture.ps1` — 前一版本夹具生成
- `desktop/tests/update/` — 升级保持、失败恢复、卸载保持套件
- `docs/desktop-data-lifecycle.md` — 43-03 数据布局与备份优先迁移契约
- `docs/desktop-installation.md` — 安装器产物与安装行为
