# PackageQualificationReport: pi-mcp-adapter

- **评估日期**: 2026-08-02
- **评估者**: NovelMind Phase 25.3 治理链（25.3-01；25.3-03 执行重验 2026-08-02）
- **对应 packages.lock.json 条目**: adopt（scope: external-tools-only）/ installed=true（25.3-03 已安装）

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

## 25.3-03 安装与合法性重验记录（2026-08-02）

- **Package Legitimacy Gate 重验**：`npm view pi-mcp-adapter@2.17.0 version license time.modified scripts dependencies repository.url`
  - version=2.17.0、license=MIT、time.modified=2026-07-31（与 RESEARCH 一致）、
    repository.url=`git+https://github.com/nicobailon/pi-mcp-adapter.git`（规范源匹配，Pitfall 1 防 typosquat）；
    scripts 仅 build/test/lint——**无 preinstall/install/postinstall**；slopcheck [OK]（RESEARCH 25.3-01 行，执行时沿用）。
- **安装**：`npm install pi-mcp-adapter@2.17.0 --save-exact` 成功（added 113 packages，无生命周期脚本执行、无 node-gyp/native build 输出）。
  `agent-service/package.json` 现为 `"pi-mcp-adapter": "2.17.0"`（精确 pin，无范围）。
- **A1（Windows 原生模块）确认**：`@napi-rs/keyring@1.3.0` 无 `install` 脚本（scripts 仅 build/prepublishOnly 等），
  Windows 预编译经 optionalDependencies 安装（`@napi-rs/keyring-win32-x64-msvc@1.3.0` → `keyring.win32-x64-msvc.node` 落盘）；
  `npm ci --ignore-scripts` 可复现（见 25.3-03-SUMMARY 验证段）。**未触发原生构建，无需 fail-closed 例外。**
- **vendoring**：`npm pack pi-mcp-adapter@2.17.0 --pack-destination vendor/pi-packages` →
  `vendor/pi-packages/pi-mcp-adapter-2.17.0.tgz`；sha256 `cab72beceb5fe32f8606b3bcf6986a8bb24c90199cb6f3440c0e79b23f1c8bef`
  已追加至 CHECKSUMS.txt；npm pack 报出的 sha512 integrity 与 package-lock.json 一致。
- **packages.lock.json**：installed 翻转为 true，integrity=`sha512-8oLD0f9ECam9aY/yUOMpchqvUGyQwlgGhZvLsNqH9QDv4oI9cyWySssfSL4ijWeXlT0+1HrHSIA0NCfr57YClw==`
  （与 package-lock.json 逐字节一致）；`network_allowlist` 种子为 stub 服务器 `stub-external-research`
  （stdio 进程无网络 egress，allowlist 记录其受治理身份；D-05 要求 network=allowlist 时非空）。
- **Pitfall 7（TS-source 加载机制）决策**：`import "pi-mcp-adapter"` 直接进 tsc 会拖入包源码并触发
  TS5097（包内 import 使用 `.ts` 扩展名）。决策：**tsconfig `paths` 把 `pi-mcp-adapter` 映射到
  `src/mcp/pi-mcp-adapter.shims.d.ts` 类型桥（类型层），运行时由 vitest/Node 解析真实包**；
  单元测试 `vi.mock("pi-mcp-adapter")` 隔离真实包；真实包 live-run 由 Task 3 人工 checkpoint 执行。
- **已知运行时注意（留给 Task 3 检查点）**：pi-mcp-adapter 顶层静态 import `@earendil-works/pi-tui`
  （optional peer，`peerDependenciesMeta.optional=true`）。闭包内确有 `@earendil-works/pi-tui@0.83.0`，
  但嵌套于 `pi-coding-agent/node_modules/` 下未提升——Node 从 pi-mcp-adapter 向上解析不到。
  单测以 mock 隔离不触此路径；live-run 需安装顶层 pi-tui（新增治理条目）或其它方案，由 Task 3 定夺。

## 结论

条件 adopt：仅限外部 MCP 服务器、仅经代理工具访问、结果只物化为 `external_evidence`
工件（D-09，`prohibited_from_canon=true`）。OAuth/keyring/MCP-UI 表面保持禁用并由
权限清单 deny。
