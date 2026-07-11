# AI-SPEC - Phase 05: Narrative Knowledge Unit Layer

## 1. System Classification

**Type:** RAG data curation, retrieval, and controlled publication.

Critical failures are unsupported units, cross-owner leakage, unsafe canonical merges, stale/deleted retrieval, frozen-test leakage, and partial promotion that leaves DB/index pointers inconsistent.

### Domain Rubric

| Dimension | Good | Bad | Stakes |
|---|---|---|---|
| Evidence support | Unit claim is directly supported by every required evidence ref | Plausible summary without support | Critical |
| Narrative identity | Character/person/event direction and aliases remain distinct | Similar names or events merge | Critical |
| Temporal validity | current/disputed/deprecated are explicit | Old and new claims collapse | High |
| Retrieval utility | Correct unit or raw evidence reaches top-k | Compression hides literal evidence | High |
| Abstention | No publish when gates or infrastructure fail | Candidate silently becomes active | Critical |

## 2. Framework Decision

Use the existing explicit FastAPI/SQLAlchemy/PostgreSQL/Chroma pipeline with Pydantic contracts. Do not add LlamaIndex, LangChain, or LangGraph: this is a linear, audit-heavy publication pipeline and the repository already owns retrieval and evaluation primitives.

## 3. Entry Pattern

```python
async def build_candidate(*, source_snapshot_id: str, config: BuildConfig) -> BuildReport:
    drafts = await materialize_units(source_snapshot_id)
    canonical = canonicalize_with_gates(drafts)
    collection = await index_immutable_candidate(canonical, config)
    return await reconcile_candidate(collection, canonical)
```

## 4. Implementation Guidance

- Accepted judgment and evidence hashes form immutable build input.
- Unit schemas use Pydantic `extra="forbid"` and require non-empty evidence refs.
- Chroma IDs derive from stable canonical unit IDs and build IDs.
- Frozen test is read-only during tuning; dev/hard-negative sets receive feedback.
- Optional LLM faithfulness judge runs only after deterministic evidence and lifecycle gates and must record model/prompt versions.

```python
class NarrativeUnitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_id: str
    question: str
    answer: str
    evidence_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    lifecycle: Literal["current", "disputed", "deprecated"]
```

## 5. Evaluation Strategy

| Dimension | Gate |
|---|---|
| Lineage/schema | 100% valid owner/work/judgment/evidence refs |
| Hard-negative merge | 0 false merges |
| Lifecycle residue | 0 deleted/deprecated IDs in active collection |
| Retrieval | Hybrid Recall@5 and MRR@5 do not regress from raw baseline; unit-only reported separately |
| Faithfulness | All promoted units pass code evidence checks; calibrated optional judge agreement >= 0.7 |
| Canary | 0 critical wrong/stale/cross-owner results |
| Reproducibility | Same input/config hashes produce identical manifest/checksum |

Use repository pytest and JSON/Markdown eval reports. Do not require external tracing SaaS; persist local run/item metrics and sanitized hashes.

## 6. Guardrails

- Block missing/out-of-scope evidence, owner mismatch, unresolved conflict, stale lifecycle, manifest mismatch, or actual-ID reconcile residue.
- Keep prior active pointer on any build/eval/promote failure.
- Human approval is required before first real active-pointer cutover.

## 7. Monitoring

Track build yield, merge/reject/review counts, units/chunks/hybrid Recall/MRR/NDCG, fallback rate, zero-result rate, latency p50/p95, candidate/active checksums, and rollback outcome.

## Checklist

- [x] Existing framework retained with rationale
- [x] Domain-specific failure modes and rubric defined
- [x] Structured output contract defined
- [x] Frozen evaluation and canary gates defined
- [x] Promotion, rollback, and lifecycle guardrails defined
