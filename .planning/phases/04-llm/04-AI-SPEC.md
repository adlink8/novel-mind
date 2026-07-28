# AI-SPEC - Phase 04: LLM 语义判定与证据门控知识图谱链路

> AI design contract generated inline because GSD subagents are not installed in this runtime.

---

## 1. System Classification

**System Type:** Hybrid: structured extraction + RAG + graph construction.

**Description:**
This phase builds an AI-assisted knowledge graph pipeline for long-form narrative text. LLMs analyze meaning and judge relationships, while deterministic scripts package evidence, validate schemas, enforce thresholds, and write accepted data. Good output is traceable: every accepted relation points back to source chunks and can be reviewed, replayed, rejected, or projected into a graph store.

**Critical Failure Modes:**
1. LLM-generated claims are written as facts without source evidence.
2. Vector similarity or same-chapter adjacency is treated as an accepted relation.
3. Fiction-only assumptions prevent history-style corpora from working.
4. Long-running extraction blocks HTTP requests or loses retry state.
5. Cross-user data leaks through graph queries, candidates, or evidence refs.

---

## 1b. Domain Context

**Industry Vertical:** Narrative understanding, literary analysis, historical source analysis.

**User Population:** Project owner, developers, readers, creators, and future researchers using NovelMind to inspect long texts.

**Stakes Level:** Medium for fiction; High for history if users treat inferred relations as factual.

**Output Consequence:** Accepted graph edges can influence analysis, search, timeline views, character/historical relation views, and later generation.

### What Domain Experts Evaluate Against

| Dimension | Good | Bad | Stakes | Source |
|---|---|---|---|---|
| Evidence grounding | Relation cites exact chunk/chapter evidence and the evidence actually supports the relation. | Relation cites vague context or unrelated chunks. | High | Local `数据分析` evidence gate pattern |
| Relation semantics | Relation type matches domain ontology and direction. | "enemy", "caused", "same event", or "follow-up" are used loosely. | High | NovelMind target graph docs |
| Temporal correctness | Event order and causality are distinguished. | Sequence is confused with causation. | High for history | Timeline model and GraphRAG research |
| Uncertainty handling | Conflicts and weak evidence route to review. | Weak evidence is auto-accepted. | High | AI eval guidance |
| Domain portability | Fiction and history both work through profiles. | Fiction labels leak into historical analysis. | Medium | User requirement |

### Known Failure Modes in This Domain

- Relationship overreach: LLM infers motives or causal links not present in text.
- Entity conflation: aliases, titles, names, and dynastic labels are merged incorrectly.
- Temporal compression: multiple events are collapsed into one because they are semantically similar.
- Direction errors: source and target are reversed in mentor, succession, cause, or conflict relations.
- Review bypass: automation accepts plausible but unsupported claims.

### Regulatory / Compliance Context

No formal regulation identified for local fiction analysis. For history, the product must label inferred or disputed relationships as such and preserve evidence/conflict metadata.

### Domain Expert Roles for Evaluation

| Role | Responsibility |
|---|---|
| Project owner | Approves ontology labels and review thresholds. |
| Literary/history reviewer | Labels a small gold set of accepted/rejected relations. |
| Developer | Maintains schema gates, deterministic metrics, and regression tests. |

---

## 2. Framework Decision

**Selected Framework:** Existing NovelMind service layer + LiteLLM + Pydantic + SQLAlchemy.

**Version:** Use current project dependencies. Do not introduce LangGraph, LlamaIndex, or Neo4j GraphRAG package in the first implementation slice.

**Rationale:**
The project already has `ai_service`, `ai_router`, SQLAlchemy async models, Chroma retrieval, BM25 search, eval tables, and owner isolation. The phase needs a deterministic, auditable pipeline more than a new orchestration framework. Adding a framework now would increase surface area before the core evidence gate exists.

**Alternatives Considered:**

| Framework | Ruled Out Because |
|---|---|
| Microsoft GraphRAG | Good reference pattern, but full indexing/community pipeline is too heavy before NovelMind has accepted relation tables. |
| Neo4j GraphRAG Python | Useful after Neo4j projection exists; not needed for MVP candidate/judgment/gate pipeline. |
| LangGraph | Useful for complex stateful workflows later; current ImportJob-style persisted jobs are enough for first phase. |
| LlamaIndex | Strong for RAG ingestion, but current Chroma/BM25 stack already works and must remain the base. |

**Vendor Lock-In Accepted:** Partial. LLM calls remain OpenAI-compatible through LiteLLM and user model configs.

---

## 3. Framework Quick Reference

### Installation

```bash
# No new framework dependency in the first slice.
cd backend
pip install -r requirements.txt -r requirements-dev.txt
```

### Core Imports

```python
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_service import ai_service
from app.services.ai_router import ai_router
```

### Entry Point Pattern

```python
class RelationProposal(BaseModel):
    source_id: str
    target_id: str
    relation_type: str
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str]
    rationale: str
    risk_flags: list[str] = []


async def judge_relation(package: dict, db: AsyncSession) -> RelationProposal:
    model = ai_router.route_task("extraction")
    response = await ai_service.chat(
        model=f"{model.provider}/{model.model_id}",
        temperature=0.1,
        max_tokens=2000,
        messages=[
            {"role": "system", "content": RELATION_JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": build_package_prompt(package)},
        ],
    )
    proposal = RelationProposal.model_validate_json(response.choices[0].message.content)
    validate_evidence_refs(proposal.evidence_refs, package)
    return proposal
```

### Key Abstractions

| Concept | What It Is | When You Use It |
|---|---|---|
| Evidence package | Bounded source facts and recall signals sent to LLM. | Every LLM proposal/judgment call. |
| Candidate | Deterministic row created before LLM judgment. | Coarse recall result. |
| Judgment | Structured LLM output plus audit metadata. | After LLM semantic evaluation. |
| Evidence gate | Deterministic validation of refs, schema, confidence, conflicts. | Before accepted projection. |
| Projection | Accepted rows materialized into relation/timeline/graph stores. | After gate passes. |

### Common Pitfalls

1. Accepting LLM JSON because it parses, without checking evidence refs.
2. Calling LLM over full chapters instead of bounded packages.
3. Mixing recall score with relation confidence.
4. Writing Neo4j first and losing PostgreSQL auditability.

### Recommended Project Structure

```text
backend/app/models/knowledge.py
backend/app/schemas/knowledge.py
backend/app/services/knowledge/
  candidates.py
  evidence.py
  llm_judge.py
  gates.py
  projection.py
  graph_sync.py
backend/app/api/knowledge.py
backend/scripts/run_knowledge_graph_pipeline.py
```

---

## 4. Implementation Guidance

**Model Configuration:**
- Use `ai_router.route_task("extraction")` for relation/entity/event extraction.
- Use temperature `0.0-0.2` for judgments.
- Require JSON-only structured outputs.
- Capture model, token counts, cost, latency, and prompt version.

**Core Pattern:**
Coarse recall is deterministic. LLM judgment is bounded and structured. Acceptance is deterministic.

**Tool Use:**
- PostgreSQL for source of truth.
- Chroma/BM25 for recall packages.
- Neo4j optional for projection only.

**State Management:**
Use persisted job/run rows for extraction runs. Each run should be resumable, idempotent, owner-scoped, and inspectable.

**Context Window Strategy:**
Never pass a full novel or full historical book. Pass compact packages containing chunk excerpts, metadata, recall signals, allowed relation types, and allowed evidence IDs.

---

## 4b. AI Systems Best Practices

### Structured Outputs with Pydantic

```python
class KnowledgeJudgment(BaseModel):
    candidate_id: str
    relation_type: str
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str]
    rationale: str
    risk_flags: list[str] = []
    needs_human_review: bool = False
```

Validation failures must not silently retry forever. Store failed output with `status="schema_failed"` and error details.

### Async-First Design

LLM calls and DB writes must be async in services. Long-running pipelines must be triggered as jobs or CLI scripts, not synchronous HTTP handlers.

### Prompt Engineering Discipline

System prompts define ontology, output schema, and forbidden behavior. User prompts contain only the evidence package. Prompt versions are stored with each judgment.

### Context Window Management

Use top-k evidence packages with strict token caps. For cross-chapter relations, send summaries and selected chunks, not entire chapters.

### Cost and Latency Budget

Start with a small gold set and batch size limits. Record per-run LLM call count, total tokens, total cost, and latency p50/p95.

---

## 5. Evaluation Strategy

### Dimensions

| Dimension | Rubric | Measurement Approach | Priority |
|---|---|---|---|
| Schema validity | 100% judgments parse into Pydantic models. | Code | Critical |
| Evidence validity | 100% accepted judgments reference in-package evidence IDs. | Code | Critical |
| Relation precision | Gold-set accepted/rejected labels match expected decision. | Human + code | High |
| Faithfulness | LLM rationale is supported by cited chunks. | LLM judge calibrated with human samples | High |
| Domain portability | Same pipeline passes fiction and history fixture sets. | Code + human sample | High |
| Cost/latency | Run stays within configured budget and time thresholds. | Code | Medium |

### Eval Tooling

**Primary Tool:** Existing pytest + custom CLI reports. Add LLM judge only after code gates pass.

**Setup:**

```bash
cd backend
pytest tests/test_knowledge_* -v
python scripts/run_knowledge_graph_pipeline.py --novel-id 1 --dry-run
```

**CI/CD Integration:**

```bash
cd backend
pytest tests/test_knowledge_models.py tests/test_knowledge_gates.py tests/test_knowledge_eval.py -v
```

### Reference Dataset

**Size:** Start with 20 examples: 10 fiction, 10 history.

**Composition:**
- supported relation
- unsupported relation
- ambiguous relation
- entity alias conflict
- temporal sequence vs causality
- direction-sensitive relationship

**Labeling:**
Project owner or reviewer labels accepted/rejected and cites expected evidence chunks.

---

## 6. Guardrails

### Online (Real-Time)

| Guardrail | Trigger | Intervention |
|---|---|---|
| Missing evidence refs | LLM output references no valid evidence. | Reject judgment. |
| Out-of-package evidence refs | LLM cites IDs not supplied. | Reject judgment and log schema/evidence failure. |
| Low confidence | Confidence below threshold or risk flags present. | Route to review queue. |
| Cross-owner evidence | Candidate package mixes owner scopes. | Block and raise security error. |

### Offline (Flywheel)

| Metric | Sampling Strategy | Action on Degradation |
|---|---|---|
| Accepted relation precision | Review all accepted edges in initial runs. | Tighten thresholds/prompts. |
| Human review rate | Sample by relation type and domain profile. | Adjust candidate recall or ontology. |
| Faithfulness disagreement | Sample LLM judge disagreements. | Calibrate judge rubric. |

---

## 7. Production Monitoring

**Tracing Tool:** Existing structured logs and AIUsageLog first. Langfuse/Arize Phoenix may be added later if observability needs exceed local logs.

**Key Metrics to Track:**
- candidates generated per run
- judgments attempted / schema failed / evidence failed / accepted / review
- accepted relation precision on gold set
- LLM token cost and latency
- Neo4j sync success/failure if enabled

**Alert Thresholds:**
- Any cross-owner evidence package: fail run.
- Evidence gate failure rate above 30%: review prompt/package generation.
- Cost exceeds configured per-run budget: stop run.

**Smart Sampling Strategy:**
Prioritize low-confidence accepted edges, high-impact relation types, history domain relations, and LLM judge/human disagreements.

---

## Checklist

- [x] System type classified
- [x] Critical failure modes identified
- [x] Domain context researched
- [x] Regulatory/compliance context noted
- [x] Domain expert roles defined
- [x] Framework selected with rationale documented
- [x] Alternatives considered and ruled out
- [x] Framework quick reference written
- [x] AI systems best practices written
- [x] Evaluation dimensions grounded in domain rubric ingredients
- [x] Eval tooling selected
- [x] Reference dataset spec written
- [x] CI/CD eval integration specified
- [x] Online guardrails defined
- [x] Production monitoring configured
