# Phase 37: Constrained Derivative Generation — Research

**Researched:** 2026-08-01
**Domain:** auditable context compilation, candidate generation, deterministic consistency gates
**Confidence:** HIGH for existing AI/retrieval boundaries; MEDIUM for new derivative schemas

<user_constraints>
## User Constraints

负责 Phase 35–39，唯一写入五个 phase 目录。三空间隔离、branch-aware retrieval、Canon 污染负向测试为硬门；Agent 只产候选，确定性代码掌握发布。Issue #29 是范围权威；Phase 22 仍 0/3 nightly。
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|---|---|---|
| REQ-FORK-03 | generation consumes auditable cutoff package and checks consistency | Reader Chat context manifest + NM retrieval manifest |
| REQ-CRE-05 | cutoff state, evidence, unresolved clues, world rules, intent are auditable | existing timeline/clue/NM source contracts |
| REQ-CRE-06 | contradiction checks, frozen set, explicit override, no original write-back | deterministic gate after model candidate |

## Summary

现有 AI 层已规定 `ai_router` 选择模型、`ai_service` 调用 provider，LLM 只输出结构化 judgment，脚本执行 schema/evidence/threshold gates；Phase 37 应把该边界用于 derivative candidate generation，而不是直接让 LLM 写入 Fanfiction/Canon 表。[CITED: docs/architecture/08-ai-model-layer.md]

Context package 应是一次冻结快照：`owner/novel/fork/space/version/cutoff/source_snapshot/policy`、人物/世界 claims、timeline causal edges、open clues、leaf evidence refs、user intent、prompt/schema/model/decoding/config hashes。Reader Chat 的 `ContextGraph` 和 NM `RetrievalManifest` 已提供可复用字段语义。[CITED: backend/app/services/reader_chat/conversations.py; backend/app/services/narrative_memory/retrieval_contracts.py]

**Primary recommendation:** deterministic compiler → allowlisted model prompt → strict candidate DTO → deterministic contradiction/character/timeline/clue gates → `candidate|blocked|needs_override`；只在显式用户动作后把 candidate 写入 derivative revision，任何原作写入路径都不存在。[CITED: ROADMAP.md#Phase 37; REQUIREMENTS.md#REQ-CRE-06]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| context package compilation | API / Backend | Database / Storage | cutoff and evidence are server-derived immutable facts |
| LLM draft candidate | API / Backend | external provider | provider has no authority to persist facts |
| consistency gates | API / Backend | Database / Storage | deterministic rules decide verdict and lineage |
| candidate review/override | Browser / Client | API / Backend | client requests explicit action; server stores override |

## Standard Stack

| Library | Version | Purpose | Evidence |
|---|---|---|---|
| LiteLLM | `>=1.83.10` | existing provider abstraction | [CITED: backend/requirements.txt; docs/architecture/08-ai-model-layer.md] |
| FastAPI/SQLAlchemy/Pydantic | existing manifest versions | API, durable jobs, strict DTOs | [CITED: backend/requirements.txt] |
| existing reader budget/job modules | repository code | lease/cancel/retry/usage lineage | [CITED: backend/app/models/reader_chat.py; backend/app/services/reader_chat/budget.py] |

No package installation is proposed. Do not add an agent framework or vector graph dependency; roadmap explicitly excludes GraphRAG/RAPTOR/LangChain as production dependencies.[CITED: ROADMAP.md v0.8 Scope Boundaries]

## Package Legitimacy Audit

No new external packages; no audit required. Any provider SDK or parser addition must be human-verified before planning install.

## Architecture Patterns

```text
fork + intent + cutoff
  -> ScopeResolver / branch-aware retrieval
  -> ContextPackage (claims + leaf refs + hashes)
  -> prompt compiler (allowlisted fields only)
  -> model -> strict CandidateDraft (no DB writes)
  -> deterministic checks: schema/evidence/fact/character/time/clue
  -> blocked | needs_override | derivative candidate revision
```

### Recommended Project Structure

```text
backend/app/services/derivative_generation/
├── context_package.py
├── prompt_contracts.py
├── candidate_contracts.py
├── consistency_gates.py
└── worker.py
backend/app/models/derivative_generation.py
backend/tests/unit/derivative_generation/
backend/tests/adversarial/test_derivative_generation_boundaries.py
```

### Pattern: agent-candidate, script-publish

The model may suggest text, references and declared divergence only from the provided package. The server verifies allowed evidence IDs, recalculates hashes, evaluates constraints and persists a derivative-only candidate.[CITED: docs/architecture/08-ai-model-layer.md; backend/app/services/knowledge/llm_judge.py]

### Anti-Patterns

- Prompting the model with raw novel retrieval without a frozen cutoff package.
- Treating semantic similarity as fact/causality/clue payoff.[CITED: docs/architecture/08-ai-model-layer.md]
- Letting an override mutate Original Canon; store an append-only derivative override.[CITED: ROADMAP.md]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| provider calls | direct SDK branches | `ai_router` + `ai_service` | central model selection, SSRF and usage logging [CITED: docs/architecture/08-ai-model-layer.md] |
| context citations | free-text source labels | `LeafCitation`/manifest | exact offsets/hash and reproducibility [CITED: retrieval_contracts.py] |
| budget/retry | in-memory counters | existing reader budget/job analog | crash/retry/cancel semantics already modeled [CITED: backend/app/models/reader_chat.py] |

## Common Pitfalls

1. **Model output contains IDs outside package:** reject candidate, do not silently drop refs.[CITED: backend/app/services/knowledge/llm_judge.py]
2. **Cutoff enforced in prompt only:** reapply on retrieval and post-check; prompts are not security boundaries.[ASSUMED]
3. **Character consistency checks only final prose:** compare structured proposed events/state deltas to cutoff state before acceptance.[ASSUMED]
4. **Allowed divergence becomes fact:** store type/reason/author/time and keep it in Fanfiction Canon only.[CITED: REQUIREMENTS.md#REQ-CRE-06]
5. **Generation success reported as quality:** separate provider success, gate verdict, sample quality and Phase 22 qualification.[CITED: STATE.md; ROADMAP.md]

## Code Examples

```python
allowed = {ref.evidence_key for ref in package.evidence_refs}
if not set(candidate.citation_keys) <= allowed:
    return Blocked(code="evidence_outside_package")
```

This is a planning sketch based on the existing evidence allowlist behavior.[CITED: backend/app/services/reader_chat/conversations.py; backend/app/services/knowledge/llm_judge.py]

## State of the Art

The repository has moved from placeholder fanfiction endpoints toward durable, versioned, candidate-only analysis; constrained derivatives should inherit that lineage rather than introduce an autonomous agent loop.[CITED: backend/app/api/fanfiction.py; backend/app/models/narrative_memory.py]

## Assumptions Log

| # | Claim | Risk |
|---|---|---|
| A1 | Existing timeline/clue/NM rows expose enough structured fields for one package compiler. | Some fields may be absent; package must report unavailable dimensions honestly. |
| A2 | Continuation and rewrite can share one candidate contract with intent enum. | Separate schemas may be needed after Issue #29 body is available. |
| A3 | Provider calls are optional in tests via deterministic fake gateway. | Live qualification needs operator/provider setup and cost controls. |

## Open Questions (RESOLVED)

- **Continuity thresholds and frozen sample count — RESOLVED:** use a versioned continuity policy plus a gold calibration corpus; thresholds are read from the policy/corpus and must never be invented during execution.
- **Permitted divergence classes — RESOLVED:** divergence is only an explicit `CanonDelta` with reason, affected evidence, actor, timestamp, approval and status; no implicit plot/character/world/timeline divergence class is accepted.

## Environment Availability

| Dependency | Available | Version | Fallback |
|---|---|---|---|
| Python | ✓ | 3.14.2 | use repository venv for backend execution |
| provider credentials | not inspected | — | deterministic fake gateway for unit/integration; live qualification blocked |
| Docker/PostgreSQL | Docker ✓ 29.6.1; DB not probed | — | SQLite unit tests; PG required for transaction/JSON constraints |

## Validation Architecture

| Property | Value |
|---|---|
| Unit/API | pytest + pytest-asyncio/httpx; `backend/pytest.ini` |
| Browser | Playwright desktop + mobile; only UAT, not implementation testing now |
| Quick command | `pytest backend/tests/unit/derivative_generation -q` (planned) |
| Full command | backend relevant unit/integration/adversarial suite |

| Req | Behavior | Test | File |
|---|---|---|---|
| REQ-CRE-05 | package has cutoff, all dimensions, refs and hashes | unit/contract | ❌ Wave 0 |
| REQ-CRE-06 | intentional fact/time/clue violations block | adversarial | ❌ Wave 0 |
| REQ-FORK-03 | model cannot cross fork or write Original | integration negative | ❌ Wave 0 |

Frozen fixtures should include a continuation, a contradictory character action, impossible timeline order, unresolved clue, missing dimension, invalid citation ID, and explicit allowed divergence. Manual UAT: inspect package, generated candidate, gate report and override audit; verify Original retrieval/eval remains unchanged.

## Security Domain

V2/V3 existing auth/session; V4 fork/owner/cutoff authorization; V5 strict schemas/evidence allowlist/size limits; V6 existing encrypted provider keys and hash lineage. Threats include prompt injection via novel text, citation forgery, future-content leakage, provider SSRF and budget exhaustion; mitigate with data/package separation, allowlists, server cutoff, existing URL validation and budget ledger.[CITED: docs/architecture/07-auth-security.md; docs/architecture/08-ai-model-layer.md]

## Sources

- HIGH: `docs/architecture/08-ai-model-layer.md`, `backend/app/services/knowledge/llm_judge.py`.
- HIGH: `backend/app/services/reader_chat/conversations.py`, `backend/app/models/reader_chat.py`.
- HIGH: `backend/app/services/narrative_memory/retrieval_contracts.py`, `ROADMAP.md`, `REQUIREMENTS.md`.
- MEDIUM: `.planning/phases/15-*/15-RESEARCH.md`, `.planning/phases/17-*/17-RESEARCH.md`.

## Metadata

Standard stack/architecture HIGH; exact product policy MEDIUM/LOW until Issue #29 is available. Valid until 2026-09-01.
