# Phase 39 Context — Derivative Export Closeout

## Decisions

- **D-39-01** 导出必须可重现，内容、章节、资产、citation、manifest、project/revision/version 必须同一导出版本对齐。Canonical refs: `REQ-FORK-05`, `REQ-CRE-07`, `ROADMAP.md#Phase 39`。
- **D-39-02** 导出只消费 Fanfiction Canon 的已发布 derivative revision 和 derivative assets；不得读取未授权的原作未来内容或修改原作空间。Canonical refs: `REQ-CRE-02`, `REQ-CRE-07`。
- **D-39-03** 交付必须包含 Markdown/EPUB、asset provenance/citation package、owner isolation 证据和三维 status report；browser UAT 与独立 audit 是发布门。Canonical refs: `REQ-FORK-05`, `ROADMAP.md#Phase 39`。
- **D-39-04** Phase 22 0/3 nightly 仍是独立风险，不因 Phase 39 UAT 通过而解除。Canonical ref: `.planning/STATE.md#Current Position`。

## Agent Consumer Contract

- Skill / mode: prepare-export.
- Inputs: approved frozen branch revisions/assets/citations.
- Official output: ExportPreparationArtifact, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: approve_export before deterministic materialize_export.
- Deterministic authority: parity/membership/hash/security/reproducibility validator.
- Forbidden: Agent-generated final bundle or download state mutation; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- 优先复用 Python 标准库/现有文件存储和 manifest hash；任何 EPUB 第三方依赖必须先做 registry/legitimacy checkpoint。
- 导出 package 的 manifest 字段和 UAT 编排可沿用现有 OpenAPI、Playwright fixture、quality status contract，但不牺牲版本 parity。

## Deferred Ideas (OUT OF SCOPE)

- 发布到第三方书店、云盘或生产 CDN；本阶段只生成本地可审计 package。
- 将导出内容注册为 Original Canon、NM candidate 或生产 active pointer。
