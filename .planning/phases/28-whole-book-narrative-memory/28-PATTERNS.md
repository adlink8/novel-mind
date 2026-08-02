# Phase 28 Patterns — Current-Code Analogs

| Planned concern | Current analog | Preserve |
|---|---|---|
| failure/recovery | narrative_memory/builder_worker.py | stable errors/checkpoints/resume |
| dependency closure | dependency_graph.py/change_oracle.py | dirty closure |
| cache carry-forward | carry_forward.py | checksum-identical only |
| chapter state | builder_repository.py/contracts.py | terminal state |
| arc/global | arc_planner.py/global_builder.py | bottom-up hierarchy |
| reporting | builder_report.py/reuse_report.py | calls/tokens/cost/cache |
| safety | narrative_memory integration/adversarial tests | no chat/no pointer |

Extend these modules and tests instead of introducing parallel orchestration. [VERIFIED: repository grep]
