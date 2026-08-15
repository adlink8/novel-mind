# NovelMind Windows Desktop 安装与打包说明（Phase 45）

本文档描述 NovelMind Windows Desktop 的安装器产物、安装行为、受支持系统矩阵与
未签名限制（D-45-01 / D-45-02 / D-45-05 / D-45-06）。打包配置在
`desktop/electron-builder.yml`，构建链在 `desktop/scripts/build-windows.ps1`，
运行时资源暂存在 `desktop/scripts/stage-runtime.ps1`。

## 产物

本地限定（unsigned local qualification）构建产出两类 artifact：

| 产物 | 位置 | 用途 |
|---|---|---|
| 解包目录 | `desktop/dist/win-unpacked/` | 免安装运行/自动化验收（`NovelMind.exe` + `app.asar` + `resources/next-standalone`） |
| NSIS 安装器 | `desktop/dist/NovelMind-Setup-<version>-x64.exe` | 用户安装（每用户安装，可改目录） |

构建命令：

```powershell
powershell -File desktop/scripts/build-windows.ps1          # 单次构建
powershell -File desktop/scripts/build-windows.ps1 -Verify  # 两次构建 + 库存比对
```

## 支持的系统矩阵

| 项 | 值 |
|---|---|
| 操作系统 | Windows 10（x64）与 Windows 11（x64） |
| 架构 | x64（打包仅产出 x64） |
| 安装位置 | 每用户（`perMachine: false`），默认 `%LOCALAPPDATA%\Programs\novel-mind-desktop`，可改目录 |
| 运行时 | 不要求用户预装 Node / Python / PostgreSQL / Docker——打包应用携带 Electron 43.3.0（内嵌 Node v24.18.1）与 Next standalone 树 |

## 41 NO-GO 打包边界（诚实限定）

Phase 41-03 结论为 **NO-GO**（见 `.planning/phases/41-electron-architecture-and-packaging-proof/41-DECISION.md`）。
本安装包**只打包已证明的运行时**：

- ✅ Electron 43.3.0 + 内嵌 Node v24.18.1（`ELECTRON_RUN_AS_NODE`，41 前置条件 #1 已证明）
- ✅ Next standalone 渲染树（`frontend/.next/standalone` + `public` + `.next/static`，哈希钉死）
- ❌ bundled Python/FastAPI、PostgreSQL/pgvector、vector store **未打包**（41-DECISION.md PREREQ-2/3/4）

因此打包后的应用对除 `next` 之外的所有运行时组件**关闭失败**（`UNSUPPORTED_IN_PACKAGED`，
`desktop/src/runtime/packaged-process-adapter.ts`），全运行时图不达到 `ready` 属预期，
直到后续计划补齐未打包前置。安装器**不会在首次运行时下载任何运行时或依赖**。

## 未签名限制（D-45-06）

- 本 artifact **未做代码签名**（`win.signAndEditExecutable: false`），Windows SmartScreen
  可能提示"未知发布者"。这是授权范围内的 unsigned-test 限制。
- 签名证书获取与发布属于外部发布门禁，需要单独授权；在证书到位前不得声称签名完成。
- 未配置 `publish`/自动更新——应用不访问发布服务器。

## 单实例与进程行为（D-45-02）

- 应用使用 Electron 单实例锁（`desktop/src/main/single-instance.ts`）：同一 `userData`
  下第二次启动**立即退出**，并把启动意图路由到已有窗口（聚焦/还原），**不启动第二个运行时图**。
- 应用及其子进程不弹出服务控制台窗口（子进程以 `windowsHide` 生成；主程序为 GUI 子系统）。
- 应用退出（正常或强制）会关闭它**拥有**的进程树，绝不按进程名杀死无关进程
  （`desktop/src/runtime/process-owner.ts`，43-02）。

## 数据与资源隔离（D-45-03 / D-45-05）

- 安装目录资源是**只读不可变输入**；所有可变数据都在版本化根目录
  `%APPDATA%/NovelMind/`（`data/`、`logs/`、`backups/`、`runtime/`、`secrets/`），
  详见 `docs/desktop-data-lifecycle.md`。
- 卸载默认**保留用户数据**（`deleteAppDataOnUninstall: false`）。清除数据是单独、明确标注的动作，
  不在本安装包范围内。
- 应用数据结构在打包前做"可变路径审计"：打包资源树中不得出现 `pgdata`、`data`、
  `logs`、`uploads`、`storage`、`secrets` 等可变状态目录（`desktop/tests/package/package-layout.test.ts`）。

## 相关代码

- `desktop/electron-builder.yml` — electron-builder 契约（appId、files 白名单、asar、extraResources、NSIS）
- `desktop/scripts/stage-runtime.ps1` — Next standalone 树 + public + `.next/static` 哈希校验暂存
- `desktop/scripts/build-windows.ps1` — 编排 tsc → stage → electron-builder → 产物审计
- `desktop/src/main/single-instance.ts` — 单实例锁与二次启动聚焦
- `desktop/tests/package/package-layout.test.ts` — 打包资源 + 可变路径审计
- `desktop/tests/package/process-behavior.windows.test.ts` — 单实例 + 进程行为
