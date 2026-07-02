---
phase: 04-llm
plan: 04-04-evaluation-and-domain-fixtures
type: implementation
wave: 4
depends_on:
  - 04-03-evidence-gates-and-projection
files_modified:
  - backend/evals/knowledge_graph_fiction_sample.json
  - backend/evals/knowledge_graph_history_sample.json
  - backend/scripts/run_knowledge_graph_eval.py
  - backend/tests/test_knowledge_eval.py
  - docs/architecture/03-data-model.md
  - docs/architecture/08-ai-model-layer.md
autonomous: false
requirements_addressed:
  - REQ-KG-04
  - REQ-KG-05
  - REQ-KG-06
truths:
  - "D-01: evaluation must prove LLM semantic judgment is separated from deterministic script gates."
  - "D-02: fixtures and evals must verify evidence-bound candidates, judgments, and accepted relations."
  - "D-03: eval reporting must separate recall signal quality from accepted graph fact quality."
  - "D-04: fiction and history fixtures use the same core graph pipeline with different ontology profiles."
  - "D-06: evals exercise persisted jobs or CLI runs, including blocked/unavailable LLM paths."
---

# 04-04 - Evaluation and Domain Fixtures

## Objective

Add enough evaluation coverage to prove the graph pipeline works for both fiction and history profiles, and that LLM judgments remain evidence-bound.

## Steps

1. Create fixture datasets.
   - Fiction: character relation, conflict, foreshadowing, event sequence.
   - History: person/organization relation, event causality, temporal sequence, source conflict.
   - Each fixture includes expected evidence refs and expected accept/reject decision.

2. Implement deterministic evaluation CLI.
   - Count candidate generation coverage.
   - Count schema failures.
   - Count evidence gate failures.
   - Measure accepted precision on labeled fixtures.
   - Measure review routing accuracy.

3. Add optional LLM-judge faithfulness check.
   - Run only after code gates pass.
   - Store judge prompt version and disagreement cases.
   - Do not block deterministic tests on live LLM availability.

4. Add cost and latency reporting.
   - Per extraction run: LLM calls, prompt tokens, completion tokens, cost estimate, latency.
   - Budget exceeded behavior: stop or route remaining candidates to pending status.

5. Update architecture docs after implementation facts are verified.
   - Data model doc: candidate/judgment/gate/projection tables.
   - AI model layer doc: extraction task routing and cost logging.
   - RAG pipeline doc: graph-augmented retrieval boundary if implemented.

6. Test, Fix, and Confirm.
   - Run deterministic eval tests.
   - Run one dry-run pipeline on fiction fixture.
   - Run one dry-run pipeline on history fixture.

## Must-Haves

- At least 20 labeled examples: 10 fiction, 10 history.
- Live LLM tests are optional/e2e; deterministic fixture tests are mandatory.
- Reports separate recall signal quality from accepted relation quality.
- Faithfulness checks cite evidence; unsupported rationales are failures.
- Human-facing docs are updated only after commands pass.
- Covers context decisions: D-01: LLM/script split; D-02: evidence-first persistence; D-03: recall signals are not truth; D-04: fiction/history profiles; D-06: persisted jobs.

## Verification

```powershell
cd backend
pytest tests/test_knowledge_eval.py -v
python scripts/run_knowledge_graph_eval.py --fixture backend/evals/knowledge_graph_fiction_sample.json --dry-run
python scripts/run_knowledge_graph_eval.py --fixture backend/evals/knowledge_graph_history_sample.json --dry-run
```

Manual verification:

- Inspect eval report for accepted/rejected examples.
- Confirm fiction/history profiles use different ontology labels but same pipeline.
- Confirm cost/latency fields are present even when cost is estimated as zero for local models.
