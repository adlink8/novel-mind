---
phase: 04-llm
plan: 04-02-candidate-packages-and-llm-judgment
type: implementation
wave: 2
depends_on:
  - 04-01-knowledge-data-contracts
files_modified:
  - backend/app/services/knowledge/candidates.py
  - backend/app/services/knowledge/evidence.py
  - backend/app/services/knowledge/llm_judge.py
  - backend/scripts/run_knowledge_graph_pipeline.py
  - backend/tests/test_knowledge_candidates.py
  - backend/tests/test_knowledge_llm_judge.py
autonomous: false
requirements_addressed:
  - REQ-KG-01
  - REQ-KG-03
  - REQ-KG-04
truths:
  - "D-01: LLM handles semantic proposal and judgment only; scripts own recall, packages, validation, and persistence."
  - "D-02: LLM outputs must cite only evidence IDs supplied in the package."
  - "D-03: vector, BM25, adjacency, entity, and time-window scores are recall signals, not graph facts."
  - "D-04: candidate packages must support both fiction and history ontology profiles."
  - "D-06: extraction and judging run as persisted jobs or CLI runs with status and retry semantics."
---

# 04-02 - Candidate Packages and LLM Judgment

## Objective

Implement the semantic middle of the graph pipeline: scripts generate bounded evidence packages, LLMs judge meaning, and judgments are stored without being accepted yet.

## Steps

1. Implement deterministic candidate recall.
   - BM25 over `text_chunks.search_vector`.
   - vector top-k through existing Chroma/vector store service.
   - same chapter and nearby chapter windows.
   - same detected entity/alias signals when available.
   - time-window signals for history profile where time refs exist.

2. Build evidence package generation.
   - Include only bounded chunk excerpts and metadata.
   - Include allowed evidence IDs.
   - Include recall signals separately from relation confidence.
   - Include allowed ontology labels for the selected profile.

3. Implement LLM proposal/judgment service.
   - Use only `ai_service.chat()` and `ai_router.route_task("extraction")`.
   - Temperature must be low (`0.0-0.2`).
   - Prompt must forbid unsupported claims.
   - Output must validate against Pydantic structured schemas.

4. Persist judgments.
   - Store raw response, parsed JSON, prompt version, model, token/cost fields when available.
   - Store `schema_failed` for invalid output.
   - Do not mark accepted in this plan.

5. Add CLI dry-run and write modes.
   - `--dry-run`: package and judge limited candidates without writes.
   - `--write`: persist run, candidates, and judgments.
   - `--limit`: hard cap LLM calls.
   - `--domain-profile fiction|history`.

6. Test, Fix, and Confirm.
   - Unit-test package construction without Chroma using mocked recall rows.
   - Unit-test LLM parser with valid/invalid JSON samples.
   - Run a dry-run on one short imported work.

## Must-Haves

- Candidate recall signals are never accepted graph facts.
- LLM receives only evidence packages, not full novels/books.
- LLM output cannot cite evidence IDs outside the package.
- Missing API key or unavailable model must produce explicit blocked status, not fake judgments.
- All prompts are versioned.
- Script supports dry-run before write.
- Covers context decisions: D-01: LLM/script split; D-02: evidence-first persistence; D-03: recall signals are not truth; D-04: fiction/history profiles; D-06: persisted jobs.

## Verification

```powershell
cd backend
pytest tests/test_knowledge_candidates.py tests/test_knowledge_llm_judge.py -v
python scripts/run_knowledge_graph_pipeline.py --novel-id 1 --domain-profile fiction --limit 5 --dry-run
```

Manual verification:

- Inspect one generated package and confirm evidence IDs are bounded.
- Confirm relation confidence is separate from vector/BM25 scores.
- Confirm LLM unavailable path does not create accepted data.
