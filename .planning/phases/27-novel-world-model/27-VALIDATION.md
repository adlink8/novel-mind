# Phase 27 Validation Strategy

| Slice | Fixture | Proof |
|---|---|---|
| Event/causal | co-occurrence vs cited cause, temporal conflict | unsupported edge rejected |
| Character epistemic | mistaken belief, hidden knowledge, transition | cutoff/POV replay consistent |
| Entities/rules | alias collision, rule exception, ownership | no false merge; exception retained |
| Authority | mixed labels + user interpretation | no silent upgrade |
| Security | owner/spoiler/chat contamination | fail closed |

Quick tests are unit per task; integration replay per wave; phase gate is:
cd backend; pytest tests/unit/world_model tests/integration/world_model
tests/adversarial/test_world_model* -q.

Human UAT at desktop/mobile: inspect state history, disclose future fact only after switch,
click evidence, and verify candidate-only status and protected overrides.
