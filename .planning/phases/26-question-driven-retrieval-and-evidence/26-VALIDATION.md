# Phase 26 Validation Strategy

| Slice | Fixture | Automated proof | Command |
|---|---|---|---|
| QueryPlan | local/analysis questions | strict schema, ambiguity, contradiction | cd backend; pytest tests/unit/queryplan/test_contracts.py tests/unit/queryplan/test_parser.py -q |
| Adapters | available/partial/missing | status, reason, provenance | cd backend; pytest tests/unit/queryplan/test_adapters.py -q |
| Fallback chain | missing exact/domain reader | exact/domain reader → deterministic heuristic candidate recall → stable partial/unavailable reason; heuristic has no fact/citation eligibility | cd backend; pytest tests/unit/queryplan/test_adapters.py tests/unit/queryplan/test_fusion.py -q |
| Fusion | frozen book, dimension toggles | deterministic ranking change | cd backend; pytest tests/unit/queryplan/test_fusion.py -q |
| Evidence | changed hash/offset | stale source fails closed | cd backend; pytest tests/adversarial/test_queryplan_evidence.py -q |
| Consumers | selection vs range | same core, distinct anchors/cutoff | cd backend; pytest tests/integration/queryplan/test_chat_consumers.py -q |
| Structured Output Integrity | alias/enum/container-shape and unsafe protected-field fixtures | conservative normalization only, strict post-repair validation, hashes/actions/warnings, unsafe repair blocked | cd agent-service; npx vitest run tests/structured-output-integrity.test.ts |
| Artifact finalizer authority | valid/blocked/invalid/cancelled normalized outputs | FastAPI integrity adapter runs before the unique finalizer write; rejected paths create 0 Artifact/Revision | cd backend; python -m pytest tests/integration/agent_runtime/test_structured_output_integrity.py -q |

Version a single-book dataset covering local, cross-chapter, causal, character, world,
unsupported, missing-reader, heuristic-candidate-only, and spoiler questions. Fingerprint
source and dataset; store expected cutoff, leaf refs, availability, fallback stage and
abstention/partial reason. Add structured-output fixtures for declared aliases, enum
canonicalization, unambiguous container shape, missing/prohibited authority fields,
ambiguous repairs, raw/repaired hashes and warnings. Run unit per task, integration per
wave, and adversarial/browser at the phase gate.

The phase gate executes the plans in dependency order: 26-04 consumer contract, 26-06
structured-output integrity, then 26-05 Skill integration. 26-05 may run only after 26-06's
normalizer and strict validator evidence is green; it must consume that shared boundary.

Human UAT: desktop and 390px mobile, ask local/cross-chapter/future questions, select an
analysis range, exercise unavailable/partial fallback, click citations, cancel/retry, and
inspect loading/error/focus behavior plus normalization warnings. This does not change
Phase 22's 0/3 status.
