# PackageQualificationReport: @earendil-works/pi-storage-sqlite-node

- **评估日期**: 2026-08-02
- **评估者**: NovelMind Phase 25.3 治理链（25.3-01）
- **对应 packages.lock.json 条目**: adopt / installed=true（scope: spike-only）

## D-02 字段

| 字段 | 值 |
|---|---|
| **source** | npm registry（官方 `@earendil-works` scope） / https://github.com/earendil-works/pi |
| **version** | 0.83.0（精确 pin，无范围） |
| **commit** | npm 分发包，无独立 commit pin；由 package-lock.json `integrity`（sha512-）锚定 |
| **license** | MIT（node_modules 内 package.json 实测） |
| **dependency tree** | 依赖 better-sqlite3 等 SQLite 原生驱动；闭包在 package-lock.json 声明树内 |
| **lifecycle scripts** | 仅 `clean`/`build`/`prepublishOnly`；**无 preinstall/install/postinstall** |
| **registered tools** | 无（仅存储注入 seam，spikes/06-storage-injection-seam.mjs 使用） |
| **filesystem behavior** | 潜在 SQLite 文件访问；按 D-05 权限清单 `filesystem: deny`（正式运行时不允许写盘） |
| **network behavior** | `network: deny`（本地存储驱动，无需网络） |
| **compatibility result** | 25.2-01 spike 06（storage injection seam）A2=PROVEN；生产代码 src/ 未引用（仅 spike 使用） |
| **adoption verdict** | **adopt（spike-only）**——仅 spike/注入缝验证用途，正式运行时严格 deny 其 filesystem/network |

## 结论

保留为 spike-only adopt。该包在 25.2 被标记为需复查（SUS，spike-only），本相位维持
该处置：不作为生产数据通道，正式运行时不授予任何文件系统/网络权限。
