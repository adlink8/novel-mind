# PackageQualificationReport: typebox

- **评估日期**: 2026-08-02
- **评估者**: NovelMind Phase 25.3 治理链（25.3-01）
- **对应 packages.lock.json 条目**: adopt / installed=true

## D-02 字段

| 字段 | 值 |
|---|---|
| **source** | npm registry / https://github.com/sinclairzx81/typebox |
| **version** | 1.3.7（精确 pin，无范围） |
| **commit** | npm 分发包；由 package-lock.json `integrity`（sha512-）锚定 |
| **license** | MIT（node_modules 内 package.json 实测） |
| **dependency tree** | 无运行时依赖（纯类型/JOI 风格 schema 库） |
| **lifecycle scripts** | **无 scripts 字段**（实测） |
| **registered tools** | 无 |
| **filesystem behavior** | 无任何文件系统访问；D-05 `filesystem: deny` |
| **network behavior** | `network: deny` |
| **compatibility result** | 25.2-05 运行时与 vitest 套件全绿；schema 用于工具注册（registry.ts） |
| **adoption verdict** | **adopt**（纯数据校验库，D-03） |

## 结论

低风险零依赖 schema 库，adopt 无保留。
