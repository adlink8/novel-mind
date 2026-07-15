---
phase: 09-dynamic-character-relationship-graph
spec_version: relationship-observation-analysis.v1
status: locked-for-planning
system_type: hybrid-structured-semantic-judgment-and-deterministic-state-machine
framework: Existing FastAPI + LiteLLM + Pydantic + SQLAlchemy
domain: fiction-only
---

# AI-SPEC — Phase 09 Dynamic Character Relationship Graph

## 1. Classification and Authority

LLM 只判断 evidence package 是否支持人物关系的建立、变化或结束。脚本拥有 candidate recall、证据包、strict validation、scope、阈值、冲突、状态机、写库、fold、override、spoiler、cache、projection 和 release。PostgreSQL accepted observation 是唯一图事实。

禁止路径：

- vector/BM25/co-occurrence/adjacency → edge
- raw LLM output/rationale → edge
- Phase 10 chat message/answer → edge
- legacy `CharacterRelation` → Phase 09 graph fact
- Neo4j → PostgreSQL 回写或 acceptance

## 2. Inputs

每个 candidate package 必须冻结：

- `owner_id`, `novel_id`, `analysis_version_id`
- Phase 04 `relation_candidate_id`, accepted `judgment_id`, judgment checksum
- source/target Character IDs and stable aliases snapshot
- allowed relation types: `ally|enemy|family|mentor|romantic`
- Phase 04 evidence refs joined to Phase 08 hierarchy/source snapshot
- each evidence: stable ID, chapter, source start/end, content hash, bounded excerpt
- deterministic recall reasons; scores remain metadata only
- prompt/schema/model/decoding/policy hashes

History profile, `causes`, `precedes`, unresolved cross-novel endpoints and evidence outside the selected analysis version are rejected before model call.

## 3. Strict LLM Output

The model output schema contains no owner, novel, DB write, status or projection fields:

```text
RelationshipSemanticJudgment
  schema_version = relationship-semantic-judgment.v1
  candidate_key: input key
  source_ref: input character ref
  target_ref: input character ref
  relation_type: ally|enemy|family|mentor|romantic
  transition: establish|change|end|uncertain
  valid_from_evidence_id: input evidence ID
  valid_to_evidence_id: input evidence ID | null
  supporting_evidence_ids: 1..8 input IDs
  confidence: 0..1
  rationale: bounded audit text
  risk_flags: allowed enum list
```

The LLM may select only IDs and enums supplied in the package. It cannot invent characters, evidence, chapters, intervals or relation labels. `uncertain` cannot auto-accept.

## 4. Script Gates and Thresholds

Gates execute in this order:

1. `source_acceptance_gate`: source judgment is still `accepted/accepted`.
2. `fiction_gate`: novel/product and source domain are fiction.
3. `scope_gate`: owner/novel/version/endpoints all match.
4. `schema_gate`: strict extra-forbid schema, allowed enum and candidate echo.
5. `evidence_gate`: all cited IDs are in package; offsets/hash/chapter/build match.
6. `interval_gate`: from <= to when bounded; anchors correspond to cited evidence narrative positions.
7. `conflict_gate`: duplicate idempotency, impossible self-edge, invalid transition chain and contradictory same-key interval are rejected/reviewed by reason code.
8. `threshold_gate`: `>=0.85` accept, `0.65..0.849` review, `<0.65` reject; any critical gate failure rejects regardless of confidence.

Threshold/policy changes create a new `policy_hash`; accepted rows are never retroactively rewritten.

## 5. State Machines

### Candidate/Judgment/Observation

```text
candidate -> judged -> gated -> accepted
                             -> needs_human_review
                             -> rejected
```

- LLM never emits these states.
- accepted observation is immutable and identified by a deterministic idempotency key over version + source judgment + endpoints + relation + interval + evidence checksum + policy hash.
- retry with the same key returns the existing accepted row; reanalysis under a new version creates a distinct chain.

### Narrative Relationship Fold

For selected position P and version V:

1. select accepted observations for V with `valid_from <= P` and no `valid_to < P`;
2. order by narrative anchor, source offset, observation ID;
3. fold transition chain deterministically per logical relationship key;
4. apply only active, uniquely relinked overrides;
5. emit current edge with machine/manual provenance.

No fold updates database rows.

### Overrides

```text
active -> superseded       (new append-only correction)
active -> needs_relink     (new version has 0 or >1 stable matches)
needs_relink -> active     (explicit human relink append action)
```

Character merge and relationship field overrides are separate contract variants but share author/reason/supersedes/evidence-signature/provenance fields.

## 6. Model Call Policy

- Reuse the existing qualified relationship semantic deployment through `AIService`; no agent framework or tools.
- `temperature=0`, structured response, provider retries 0, stream false.
- At most one persisted same-deployment schema repair with deterministic error codes; no model fallback inside one lineage.
- The original Phase 04 accepted judgment may make a second model call unnecessary when interval/transition semantics are deterministically complete. A `call_skipped` audit is required in that case.
- Unknown deployment capability, price, timeout/refusal, malformed second response or budget failure pauses/rejects before acceptance; it never creates an edge.

## 7. Evidence Packaging and Prompt Injection

System prompt contains ontology, transition definitions, evidence-only rule and schema. User message contains only delimited fiction excerpts and stable IDs. Text that says “ignore instructions”, SQL, JSON commands or graph assertions remains untrusted novel data.

Packages are bounded by the accepted judgment evidence set plus adjacent Phase 08 evidence needed to resolve interval boundaries. The model never receives the full novel, hidden future evidence beyond the relationship build's frozen version, user secrets or chat content.

## 8. Exact Cache and Persistence

Exact cache key includes:

`source_snapshot + hierarchy_checksum + analysis_version + source_judgment_checksum + evidence_package_hash + prompt_hash + schema_hash + deployment_revision + decoding_hash + policy_hash`.

Only locally validated complete output is cacheable. Failure, refusal, outcome unknown and review status are not semantic facts. Cache hit writes an audit linked to the original successful attempt and artifact checksum.

Persistence contracts:

- `RelationshipBuildRun`: owner/novel/version, state/checkpoint, prompt/schema/model/policy lineage, counts/error.
- `RelationshipObservationCandidate`: deterministic package and source judgment lineage.
- `RelationshipObservationJudgment`: raw hash, structured output, model/usage/cost/latency, gate statuses.
- `RelationshipObservation`: immutable accepted fact, interval, evidence checksum, idempotency key.
- `RelationshipEvidenceLink`: normalized evidence refs.
- `CharacterIdentityOverride` and `RelationshipOverride`: append-only protective corrections.
- `RelationshipProjectionAudit`: replay manifest/checkpoint; never authoritative.

## 9. Architectural Responsibility Map

| Responsibility | Owner | Forbidden owner |
|---|---|---|
| Candidate recall and package | deterministic relationship service | LLM, frontend |
| Semantic relation/transition judgment | LLM through AIService | DB trigger, vector score |
| Schema/evidence/scope/threshold/conflict | deterministic gates | LLM |
| Observation write/state/idempotency | PostgreSQL service transaction | LLM, Neo4j, browser |
| Narrative fold/override overlay | query service | Cytoscape, LLM |
| Spoiler cutoff and visible metadata | owner-scoped API/query service | browser-only filtering |
| Graph layout/selection | Cytoscape.js | PostgreSQL, LLM |
| Timeline rendering | existing ECharts component | Cytoscape.js rewrite |
| Neo4j replay | projection adapter from accepted rows | acceptance/write-back |

## 10. Evaluation Gates

Critical deterministic gates:

- accepted facts with invalid evidence/scope/history/vector-only/chat-only source: zero
- future node/edge/type/filter/count/evidence leakage: zero
- cross-owner/version response leakage: zero
- accepted observation mutation after creation: zero
- automatic override mis-relink: zero
- Neo4j replay checksum mismatch: zero

Semantic quality on frozen fiction fixtures:

- relation-type macro precision >= 0.92
- transition precision >= 0.90
- interval anchor exact-match >= 0.90
- critical false accept = 0
- review routing recall on ambiguity/adversarial cases >= 0.95

Operational gates record calls/tokens/cost/latency, schema repair rate, cache hit, accepted/review/rejected counts, projection replay and query/browser performance. LLM judge may assist descriptive faithfulness review only after calibration; it cannot decide acceptance or release alone.

## 11. Downstream Contract

- Phase 10 receives only the already-filtered read model; chat text and answers are never candidate sources.
- Phase 11 may store foreign references to accepted observation IDs/evidence; clue state remains Phase 11 authority.

## 12. Explicit Non-Goals

No history, autonomous agents, tools, semantic cache, graph prediction, conversation persistence, clue lifecycle, direct Neo4j truth, or UI exposure of analysis summaries.

