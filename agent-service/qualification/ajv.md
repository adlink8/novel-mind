# PackageQualificationReport: ajv

- **评估日期**: 2026-08-02
- **评估者**: NovelMind Phase 25.3 治理链（25.3-01）
- **对应 packages.lock.json 条目**: adopt / installed=true

## D-02 字段

| 字段 | 值 |
|---|---|
| **source** | npm registry / https://github.com/ajv-validator/ajv |
| **version** | 8.20.0（精确 pin，无范围） |
| **commit** | npm 分发包；由 package-lock.json `integrity`（sha512-）锚定 |
| **license** | MIT（node_modules 内 package.json 实测） |
| **dependency tree** | 依赖 fast-deep-equal、json-schema-traverse、uri-js、fast-uri（均已在闭包声明树内） |
| **lifecycle scripts** | 仅 `build`/`test`/`bundle`/`prepublish`；**无 preinstall/install/postinstall** |
| **registered tools** | 无 |
| **filesystem behavior** | 无主动文件访问；D-05 `filesystem: deny` |
| **network behavior** | `network: deny` |
| **compatibility result** | 25.2-05 权限清单/锁文件 schema 校验（loader.ts）与 vitest 套件全绿 |
| **adoption verdict** | **adopt**（纯 JSON Schema 校验库，D-03） |

## 结论

JSON Schema 校验是 25.3-02 权限清单验证的既定工具（复用，不新增第二套校验库），adopt 无保留。
