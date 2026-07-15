---
phase: 11-clue-and-foreshadow-tracking
spec_version: clue-foreshadow.v1
status: locked-for-planning
system_type: Hybrid (deterministic cross-chapter recall + strict semantic judgment + script-owned lifecycle)
framework: Existing FastAPI + SQLAlchemy + Pydantic + LiteLLM
domain: fiction
---

# AI-SPEC — Phase 11: Clue and Foreshadow Tracking

## 1. System Classification

This is not an autonomous agent and not a writing system. It is a versioned evidence-classification pipeline.

~~~
Phase 07 evidence + Phase 08 visible timeline
  -> deterministic cross-chapter recall
  -> bounded cue/later evidence package
  -> LLM semantic judgment
  -> local schema/evidence/threshold/conflict/temporal gates
  -> append-only lifecycle candidate|active|reinforced|paid_off|dismissed
  -> spoiler-safe API projection + protected human override
~~~

### Authority boundary

| Component | Allowed | Forbidden |
|---|---|---|
| Recall script | produce candidates/reason codes/scores | publish state |
| LLM | classify cue/reinforcement/payoff relationship, cite allowed IDs, abstain | tools, DB, state, version, budget, write, link mutation |
| Validator/gates | schema, evidence, offsets, order, thresholds, conflicts | invent semantic support |
| Lifecycle service | append legal transition after gates | update/delete history |
| Human override service | append confirm/reject/note/link adjustment | mutate machine version |
| Query service | owner/spoiler filter then derive state | expose hidden-derived data |

Critical failures:

1. paid_off without independent early cue and later payoff evidence;
2. similarity, co-occurrence, chat text or unsupported relation observation becoming fact;
3. future payoff leaking through status/count/filter/link metadata;
4. reanalysis overwriting human disposition;
5. retry/budget/version failure creating duplicate calls or pointer movement;
6. Phase 09/10 absence being silently replaced by fabricated data.

## 2. Framework Decision

Use the existing stack. Do not add LangChain, LangGraph, LlamaIndex, Agents SDK, a new vector store or a tracing SaaS.

Pydantic validates model output locally even when provider structured output is enabled. LiteLLM provider retries are disabled; the durable worker owns a single explicitly budgeted repair attempt. Every deployment revision and price snapshot must be frozen in version lineage.

## 3. Strict Model Output

The model output contains semantic claims only:

~~~python
class ClueSemanticJudgment(StrictModel):
    schema_version: Literal["clue-semantic-judgment.v1"]
    candidate_id: str
    classification: Literal[
        "cue_only", "reinforcement", "payoff", "unrelated", "ambiguous"
    ]
    cue_evidence_ids: list[str]
    later_evidence_ids: list[str]
    confidence: float
    conflict_flags: list[Literal[
        "MOTIF_ONLY", "ORDER_CONFLICT", "ENTITY_CONFLICT",
        "UNRESOLVED_REFERENCE", "INSUFFICIENT_PAYOFF"
    ]]
    rationale: str
~~~

The schema has no status, publish, database ID, SQL, tool call, cost, version pointer or arbitrary link field. extra="forbid"; list lengths and rationale length are bounded.

## 4. Evidence Package Contract

Each request includes:

- owner/novel scope identifiers only as inert metadata;
- source snapshot, active hierarchy build/checksum and selected timeline version/checksum;
- one early cue window and bounded later windows;
- evidence IDs, chapter IDs, source offsets and content hashes;
- deterministic recall reason codes and scores;
- allowed classifications/conflict enums;
- total token ceiling and package hash.

The model can cite only supplied IDs. Full novel, full chat history and hidden future chapters outside the analysis job's authorized scope are not sent. Novel text is delimited as untrusted data; embedded instructions are never executed.

### Context limits

- early cue: at most 3 evidence units;
- later windows: at most 8 evidence units across at most 4 chapters;
- input: at most 12,000 tokens;
- output: at most 1,200 tokens;
- timeout: 45 seconds;
- provider retries: 0;
- repair: at most 1 persisted same-deployment attempt after schema failure.

Oversized packages are deterministically trimmed by recall score, narrative distance and source order while preserving whole evidence units. A trimmed package records omitted IDs and cannot claim a payoff requiring omitted evidence.

## 5. Deterministic Gates

Gates execute in this order:

1. owner/novel/version/build scope;
2. strict schema and enum;
3. allowed evidence membership;
4. exact offset/content hash;
5. cue/later narrative order;
6. state transition legality;
7. minimum confidence and conflict policy;
8. human override protection;
9. immutable version/pointer policy.

Gate outcomes and reason codes are persisted. The LLM confidence is one input; it never bypasses a gate.

### State evidence

| State | Required proof |
|---|---|
| candidate | deterministic recall package and at least one potential cue evidence |
| active | validated cue evidence + accepted semantic cue judgment or human confirm |
| reinforced | active + new later reinforcing evidence not used by prior event |
| paid_off | reinforced + original cue + distinct later payoff + semantic payoff judgment + strict narrative order |
| dismissed | append-only machine conflict disposition or human reject; prior evidence/history retained |

## 6. Durable Execution, Cache and Budget

The worker uses clue-owned PostgreSQL run/version/call/budget tables. It claims leases, writes stable stage checkpoints and can resume after restart.

Exact cache key:

~~~
stage
+ source_snapshot_hash
+ hierarchy_build_id/checksum
+ timeline_version_id/checksum
+ candidate/package_hash
+ prompt_hash
+ schema_hash
+ resolved_model_revision
+ decoding_hash
+ gate_config_hash
~~~

Only complete locally validated outputs are cached. Semantic cache is forbidden. Cache hits write call-skipped audit records.

Before each provider call, reserve worst-case calls/input/output/cost in a short transaction. Unknown pricing, missing capability, exhausted budget or failed reservation pauses before network access. Outcome-unknown calls remain charged/reserved until explicitly resolved.

## 7. Phase 09/10 Source Policy

- Versioned relationship observations may be recalled only through a read-only protocol and must resolve to primary evidence plus owner/novel/version.
- Read-only conversation sources may return selected-text/citation references, never free-form chat assertions as evidence.
- Default null sources produce no signals.
- The clue LLM package never contains unverified chat answers.
- Phase 11 writes no relation observation, graph edge, conversation, message or timeline event.

## 8. Human Override and Reanalysis

Human actions are not LLM input instructions and are never overwritten:

- confirm: append protected candidate→active;
- reject: append protected nonterminal→dismissed;
- annotate: append note override without state change;
- adjust links: append superseding link override.

Reanalysis creates a new immutable machine version. Stable evidence identity may relink an override only on exactly one match. Ambiguous/no match sets needs_relink. Version comparison reports added/removed/changed machine candidates, lifecycle differences and override application separately.

## 9. Spoiler Policy

The model worker may analyze the full authorized novel for an explicit deep-analysis run, but user-facing queries obey Phase 08 reading visibility.

At query time:

1. resolve existing timeline_full_book preference;
2. filter lifecycle evidence/events by cutoff;
3. recompute current visible state from filtered events;
4. remove links whose supporting evidence or target is hidden;
5. derive filters/counts/chains;
6. apply visible overrides.

Therefore a machine-paid_off clue can appear only active or reinforced to a reader before the payoff chapter.

## 10. Evaluation

| Dimension | Release gate |
|---|---|
| strict schema | 100% persisted judgments valid |
| evidence/scope/order | 100%; critical failures 0 |
| critical false active/paid_off | 0 on adversarial fixture |
| paid_off precision | ≥ 0.90 |
| active/reinforced macro F1 | ≥ 0.85 |
| lifecycle replay | 100% equals API state |
| spoiler leakage | 0 across rows/counts/filters/links/chains |
| override preservation | 100%, ambiguous relink never auto-applied |
| cost | ≤ frozen policy |
| model latency | p95 ≤ 60 s |
| API latency | p95 ≤ 500 ms on frozen local dataset |
| reproducibility | same lineage/transcripts → same manifest checksum |

Live provider qualification is separate from PR tests. Missing credentials, quota, pricing, PostgreSQL or browser dependencies produce blocked metrics and cannot pass.

## 11. Prompt Discipline

- System prompt defines semantic labels, evidence-only behavior, abstention and schema.
- User prompt contains only the frozen package and untrusted text delimiters.
- 6–10 fixed examples cover motif-only false positive, reinforcement, genuine payoff, order conflict and ambiguity; example bank hash enters lineage.
- The model is instructed to choose unrelated/ambiguous rather than infer author intent without evidence.
- Raw response is retained only by hash plus bounded/audited payload policy; credentials and unnecessary full text are never logged.

## 12. Explicit Non-Goals

- history support
- writing/continuation
- relationship graph or chat implementation
- modifying Phase 09/10
- exposing summary intermediates
- autonomous tools or model-selected publication
- similarity/chat content as fact

## Checklist

- [x] LLM/script authority is explicit
- [x] cue/reinforcement/payoff evidence gates are strict
- [x] lifecycle is append-only and human overrides are protected
- [x] Phase 09/10 absence is handled by null read-only protocols
- [x] exact cache, durable attempts and pre-call budget are defined
- [x] spoiler projection derives state only after cutoff
- [x] frozen/adversarial/live/release gates are measurable

