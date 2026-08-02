# PackageQualificationReport: pi-mcp-adapter

- **评估日期**: 2026-08-02
- **评估者**: NovelMind Phase 25.3 治理链（25.3-01）
- **对应 packages.lock.json 条目**: adopt（scope: external-tools-only）/ installed=false（由 25.3-03 翻转为 true）

## D-02 字段

| 字段 | 值 |
|---|---|
| **source** | npm registry / https://github.com/nicobailon/pi-mcp-adapter（26.5 个月龄、~88k 周下载、slopcheck [OK]） |
| **version** | 2.17.0（精确 pin，无范围） |
| **commit** | npm 分发包；发布/修改日期 2026-07-31；registry integrity 锚定（当前未安装） |
| **license** | MIT |
| **dependency tree** | 11 个直接依赖：`@modelcontextprotocol/sdk`、**`@napi-rs/keyring`**（原生预编译模块，Windows 检查 A1 延后至 25.3-03，需在资质报告记录 fail-closed 例外）、`open`、`cross-spawn`、`recheck`、`smol-toml`、`zod`、`ajv` 等 |
| **lifecycle scripts** | **无 postinstall/preinstall/install**（RESEARCH 已核验 npm registry scripts 字段，仅 build/test/lint） |
| **registered tools** | 单一 lazy 代理工具 `mcp`（D-07：`directTools: false`，`hostConfigDiscovery: "off"`，仅 allowlist 外部 MCP 服务器） |
| **filesystem behavior** | 以 `createMcpAdapter({config})` 隔离快照模式使用，不合并 `~/.agents`/`.mcp.json` 等 ambient 配置（Pitfall: 文件模式违反 D-07）；权限清单 `filesystem: deny` |
| **network behavior** | `network: allowlist`，`network_allowlist` 为空种子（由 25.3-03 填充外部 MCP 主机）；绝不接触 NovelMind PostgreSQL / Original Canon（D-08） |
| **compatibility result** | TypeScript 源码包（Pitfall 7），需 TS-capable loader；engines node>=20 满足本地 Node 22；Windows @napi-rs/keyring 原生预编译检查 A1 延后并 fail-closed |
| **adoption verdict** | **adopt（条件：external-tools-only）**——外部研究/外部文档/图像生成服务的唯一 MCP 消费入口，D-07 |

## 结论

条件 adopt：仅限外部 MCP 服务器、仅经代理工具访问、结果只物化为 `external_evidence`
工件（D-09，`prohibited_from_canon=true`）。OAuth/keyring/MCP-UI 表面保持禁用并由
权限清单 deny。
