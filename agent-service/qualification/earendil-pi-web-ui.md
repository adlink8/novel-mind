# PackageQualificationReport: @earendil-works/pi-web-ui

- **评估日期**: 2026-08-02
- **评估者**: NovelMind Phase 25.3 治理链（25.3-01）
- **对应 packages.lock.json 条目**: pattern-only / installed=false

## D-02 字段

| 字段 | 值 |
|---|---|
| **source** | npm registry（官方 `@earendil-works` scope，与已验证的 25.2 SDK 同发布者）/ npm 未链源码仓库 |
| **version** | 0.75.3（精确 pin，仅作参考记录，不安装） |
| **commit** | npm 分发包；发布 2026-05-27；**未安装，无 integrity** |
| **license** | MIT |
| **dependency tree** | 重且部分非常规：`pdfjs-dist`（精确 pin）、CDN xlsx tarball、`jszip`、`docx-preview`、`ollama`、`@lmstudio/sdk` —— 强化不安装决定 |
| **lifecycle scripts** | 无 postinstall/preinstall/install（RESEARCH 核验） |
| **registered tools** | 渲染器模式捐赠者：`registerToolRenderer`/`registerMessageRenderer` 组件注册表、紧凑工具结果卡片设计 |
| **filesystem behavior** | 上游 mini-lit + Tailwind v4 + IndexedDB 存储假设与 NovelMind React/Next 栈冲突；不采纳 |
| **network behavior** | 含浏览器 key 模型（`ollama`/`@lmstudio/sdk`）；不采纳 |
| **compatibility result** | 与 SDK 0.83.0 存在版本漂移（0.75.3 vs 0.83.0）；**明确拒绝** ChatPanel/IndexedDB SSOT/provider-key 采纳（25.3-05 渲染器原型仅借注册表/卡片模式） |
| **adoption verdict** | **pattern-only（选择性渲染器模式）**——设计参考，绝非依赖 |

## 结论

仅借 `registerToolRenderer` 风格的组件注册与紧凑卡片设计到 25.3-05 的
CitedAnswerArtifact 渲染器；任何 package.json 引入本包即违反 D-03（tests/ci 静态
门禁禁止）。
