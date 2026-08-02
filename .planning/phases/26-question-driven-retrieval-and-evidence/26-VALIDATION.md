# Phase 26 Validation Strategy

| Slice | Fixture | Automated proof | Command |
|---|---|---|---|
| QueryPlan | local/analysis questions | strict schema, ambiguity, contradiction | cd backend; pytest tests/unit/queryplan/test_contracts.py tests/unit/queryplan/test_parser.py -q |
| Adapters | available/partial/missing | status, reason, provenance | cd backend; pytest tests/unit/queryplan/test_adapters.py -q |
| Fusion | frozen book, dimension toggles | deterministic ranking change | cd backend; pytest tests/unit/queryplan/test_fusion.py -q |
| Evidence | changed hash/offset | stale source fails closed | cd backend; pytest tests/adversarial/test_queryplan_evidence.py -q |
| Consumers | selection vs range | same core, distinct anchors/cutoff | cd backend; pytest tests/integration/queryplan/test_chat_consumers.py -q |

Version a single-book dataset covering local, cross-chapter, causal, character, world,
unsupported, and spoiler questions. Fingerprint source and dataset; store expected cutoff,
leaf refs, availability, and abstention reason. Run unit per task, integration per wave,
and adversarial/browser at the phase gate.

Human UAT: desktop and 390px mobile, ask local/cross-chapter/future questions, select an
analysis range, click citations, cancel/retry, and inspect loading/error/focus behavior.
This does not change Phase 22's 0/3 status.
