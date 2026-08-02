# Phase 29 Patterns — Current-Code Analogs

| Planned concern | Current analog | Preserve |
|---|---|---|
| dataset/run/result | backend/app/models/eval.py and eval_service.py | owner isolation and lineage |
| frozen fixture | narrative_memory/qualification_fixtures.py and backend/evals | versioned single-book input |
| metrics | qualification_metrics.py | bucket metrics |
| verdict | qualification_verifier.py and qualification_verdict.py | blocked is valid |
| citations | reader_chat schemas/context | leaf allowlist |
| browser UAT | frontend/e2e/reader-chat*.spec.ts and analysis panel tests | desktop/mobile paths |

Map new gold questions/report fields into these artifacts; do not create a parallel QA
database. [VERIFIED: repository grep]
