# Phase 21 Branch Delta Spike Manifest

## Question

以 `origin/master@01503c2` 为唯一执行基线时，`feat/phase21-debtfix@7c14119`
的 41 个非等价提交应如何处置，才能保留有效需求而不回滚 Phase 23–25.1 已在
`master` 落地的治理、索引与分析对话契约？

## Evidence Snapshot

- captured_at: `2026-07-31`
- baseline: `origin/master@01503c29209b4b6a1b4caa5284fc04d4e38debe5`
- compared_branch: `feat/phase21-debtfix@7c14119cc5d2b78f1c9d0fe718832b5d7d25ddea`
- divergence: `master-only=38`, `branch-only=41`
- method:
  - `git rev-list --left-right --count origin/master...feat/phase21-debtfix`
  - `git log --left-right --cherry-pick origin/master...feat/phase21-debtfix`
  - `git diff --name-status origin/master feat/phase21-debtfix`
  - `git cherry -v origin/master feat/phase21-debtfix`

## Classification

| Class | Count | Representative commits | Disposition |
|---|---:|---|---|
| equivalent merged | 0 | none (patch-id/cherry comparison found no equivalent branch-only commit) | Do not claim implicit merge |
| missing on master | 19 | `59d9593..8000818` bookmarks/index recovery; `5fa6cf9..09b23ff` reader modes/performance; `6d9984e..edf2b0d` image generation; `fe1a823..7c14119` full-analysis orchestration/progress | Preserve as requirements and reimplement in new phases |
| obsolete | 9 | `6f8d0f6`, `431af28`, `184e6b7`, `f8951a6`, `4f8e923`, old migration-chain adjustments | Do not cherry-pick; superseded by master contracts/toolchain |
| reimplement | 13 | `903e892`, `1147313`, `b48a3ac`, `060f719`, `1b40ed2`, `4c94a5b`, `b54d382`, `3c4252e`, `d35ca91`, `7a6939a`, `0bf1f1a`, `c354e77`, `596514b` | Rebuild against Phase 23–25.1 authorities with fresh tests |

## Requirements

- `master` is the sole GSD execution baseline.
- No bulk merge or cherry-pick from `feat/phase21-debtfix`.
- Branch-only product value is routed into Phase 26–39 or an explicit backlog.
- Master-only ADRs, indexing journal/reconcile gate, retrieval router, facet honesty,
  relationship provenance, clue cost and analysis-chat range contracts remain authoritative.
- Status reporting separates `implementation_readiness`, `sample_data_coverage`, and
  `quality_qualification`.

## Verdict

`VALIDATED`: the old branch is a requirement/evidence source, not an integration source.
The safe path is selective reimplementation on `master`.
