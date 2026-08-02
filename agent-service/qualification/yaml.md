# PackageQualificationReport: yaml

- **评估日期**: 2026-08-02
- **评估者**: NovelMind Phase 25.3 治理链（25.3-01）
- **对应 packages.lock.json 条目**: adopt / installed=true

## D-02 字段

| 字段 | 值 |
|---|---|
| **source** | npm registry / https://github.com/eemeli/yaml |
| **version** | 2.9.0（精确 pin，无范围） |
| **commit** | npm 分发包；由 package-lock.json `integrity`（sha512-）锚定 |
| **license** | ISC（node_modules 内 package.json 实测） |
| **dependency tree** | 无运行时依赖（rollup 仅构建期 devDep） |
| **lifecycle scripts** | 仅 `build`/`test`/`docs`/`prepublishOnly`；**无 preinstall/install/postinstall** |
| **registered tools** | 无 |
| **filesystem behavior** | 无主动文件访问；D-05 `filesystem: deny` |
| **network behavior** | `network: deny` |
| **compatibility result** | 25.2-05 skill loader（skill.yaml 解析）与 vitest 套件全绿 |
| **adoption verdict** | **adopt**（纯解析库，D-03） |

## 结论

ISC 许可证在治理白名单（MIT|BSD-*|ISC|Apache-2.0）内，adopt 无保留。
