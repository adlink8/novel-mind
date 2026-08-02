# PackageQualificationReport: @gotgenes/pi-permission-system

- **评估日期**: 2026-08-02
- **评估者**: NovelMind Phase 25.3 治理链（25.3-01）
- **对应 packages.lock.json 条目**: pattern-only / installed=false

## D-02 字段

| 字段 | 值 |
|---|---|
| **source** | npm registry / https://github.com/gotgenes/pi-packages（3 个月龄、~7.6k 周下载、slopcheck [OK]） |
| **version** | 24.0.0（精确 pin，仅作语义参考记录，不安装） |
| **commit** | npm 分发包；修改日期 2026-07-26；**未安装，无 integrity** |
| **license** | MIT |
| **dependency tree** | `tree-sitter-bash`、`web-tree-sitter`、`zod` —— **不采纳**（这些依赖用于解析 bash，NovelMind 域动作模型永不解析 bash） |
| **lifecycle scripts** | 无 postinstall/preinstall/install（RESEARCH 核验） |
| **registered tools** | 无（语义捐赠者：allow/ask/deny 优先级、fail-closed、session 审批、tool-visibility 过滤） |
| **filesystem behavior** | 上游是文件路径/bash 为中心的数据模型，NovelMind 不继承；域动作模型不涉及 host 文件系统 |
| **network behavior** | 不安装 → 无网络面 |
| **compatibility result** | 其优先级语义（deny > ask > allow、last-match-wins、fail-closed 夹紧、restrict-only 过滤）在 25.3-04 以 clean-room 方式移植到 `src/policy/engine.ts`（RESEARCH Pattern 4） |
| **adoption verdict** | **pattern-only**（不安装）——作为 D-03 "fork/extract policy core" 的语义来源，以 clean-room 提取满足，避免拖入 tree-sitter 原生依赖/TUI/`~/.pi` 配置机件 |

## 结论

不安装、不引入依赖树；只取文档化语义做 clean-room 重实现。任何 package.json 引入
本包即违反 D-03（tests/ci 静态门禁禁止）。
