# PackageQualificationReport: @earendil-works/pi-agent-core

- **评估日期**: 2026-08-02
- **评估者**: NovelMind Phase 25.3 治理链（25.3-01）
- **对应 packages.lock.json 条目**: adopt / installed=true

## D-02 字段

| 字段 | 值 |
|---|---|
| **source** | npm registry（官方 `@earendil-works` scope） / https://github.com/earendil-works/pi |
| **version** | 0.83.0（精确 pin，无范围） |
| **commit** | npm 分发包，无独立 commit pin；由 package-lock.json `integrity`（sha512-）锚定 |
| **license** | MIT（node_modules 内 package.json 实测） |
| **dependency tree** | 依赖 Pi SDK 全家（pi-ai、pi-coding-agent 等 0.83.0 同版本集）与常见工具链；闭包全部在 package-lock.json 声明树内，无未声明新人 |
| **lifecycle scripts** | 仅 `clean`/`build`/`test`/`prepublishOnly`；**无 preinstall/install/postinstall**（实测 package.json） |
| **registered tools** | 无（agent-service 内通过 25.2-05 registry 暴露的 7 个 domain tools 由本包承载，工具名由 DOMAIN_TOOL_NAMES 冻结） |
| **filesystem behavior** | 自身不含读写 host 文件系统的主动行为；按 D-05 权限清单为 `filesystem: deny` |
| **network behavior** | 支持连接 AI provider（Anthropic/OpenAI/Google/DeepSeek 等）；权限清单 `network: allowlist`，`network_allowlist` 仅含外部 provider 域名 |
| **compatibility result** | 25.2-01 spike 7/7 ALL PASS、25.2-05 运行时 66 项 vitest 通过；与 Node >=22.19 兼容 |
| **adoption verdict** | **adopt**（核心运行库，D-03） |

## 结论

作为 Phase 25.2 已落地并验证的核心运行库继续 adopt；其依赖闭包无危险 lifecycle，
版本被 package-lock.json + packages.lock.json 双重锚定。禁止动态 pi install / pi update
引入其它版本（D-04）。
