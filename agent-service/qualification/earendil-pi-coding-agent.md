# PackageQualificationReport: @earendil-works/pi-coding-agent

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
| **dependency tree** | 闭包较大（~319 项，含 AWS SDK/Bedrock、Anthropic/OpenAI SDK、google-genai、protobufjs 等）；全部在 package-lock.json 声明树内。**注意**：google-genai 的 `preinstall` 为 echo 空操作、protobufjs 的 `postinstall` 已人工审计（仅打印版本方案警告），scan-packages 已记录放行 |
| **lifecycle scripts** | 本包仅 `build`/`test`/`prepublishOnly`；**自身无 preinstall/install/postinstall**。其传递依赖的 2 个 lifecycle 脚本经 scan-packages 审计为无害（见 25.3-01-SUMMARY） |
| **registered tools** | 承载 agent 会话循环/工具注入；NovelMind 侧工具名由 DOMAIN_TOOL_NAMES 冻结，不采用本包自带 CLI 工具集 |
| **filesystem behavior** | 无主动 host 文件读写；D-05 `filesystem: deny` |
| **network behavior** | 支持多 provider（Anthropic/OpenAI/Bedrock/Google 等）；权限清单 `network: allowlist`，仅外部 provider 域名 |
| **compatibility result** | 25.2-01 spike（loader closure/lifecycle/events/storage seam/skill injection）7/7 通过；25.2-05 运行时全绿 |
| **adoption verdict** | **adopt**（核心运行库，D-03） |

## 结论

继续 adopt。闭包内 2 个传递 lifecycle 脚本经人工审计与 scan-packages 白名单放行；
安装路径固定 `npm ci --ignore-scripts`（D-04），脚本实际不执行。禁止任何运行时
pi install / pi update 改动该闭包（deny）。
