# PackageQualificationReport: @earendil-works/pi-ai

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
| **dependency tree** | 依赖 model 数据目录与 provider 适配层；闭包全部在 package-lock.json 声明树内，无未声明新人 |
| **lifecycle scripts** | 仅 `build`（含 `generate-models`）/`test`/`prepublishOnly`；**无 preinstall/install/postinstall** |
| **registered tools** | 无（提供 ModelRuntime/Provider 层；agent-service 的网关 provider 由此构建，见 src/agent/provider.ts） |
| **filesystem behavior** | 无主动 host 文件读写；D-05 `filesystem: deny` |
| **network behavior** | 通过 `openai-completions` API 与 NovelMind 网关通信（配置式，非 ambient）；权限清单 `network: allowlist`，`network_allowlist` 仅含外部 provider 域名 |
| **compatibility result** | 25.2-02 facade 89 项 contract + 47 项 adversarial 通过；agent-service 网关 provider 测试通过 |
| **adoption verdict** | **adopt**（核心运行库，D-03） |

## 结论

继续 adopt。provider 密钥等敏感信息仅以 env（secrets: named-only）注入，包本身
不读取 `~/.pi` 等用户级配置（D-04：user-global 包从不加载）。
