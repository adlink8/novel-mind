# Phase 37 Patterns — File-to-Analog Map

| 拟改/新增文件 | 当前代码 analog | 应复用的模式 |
|---|---|---|
| `backend/app/services/derivative_generation/context_package.py` | `reader_chat/context.py`, `reader_chat/conversations.py` | frozen manifest, visible cutoff, evidence refs |
| `backend/app/services/derivative_generation/candidate_contracts.py` | `knowledge/llm_judge.py`, `narrative_memory/builder_contracts.py` | model outputs strict schema only |
| `backend/app/services/derivative_generation/consistency_gates.py` | `clues/gates.py`, timeline evidence/gates | deterministic evidence/conflict verdict |
| `backend/app/services/derivative_generation/worker.py` | `reader_chat/worker.py`, `timeline/worker.py` | durable job lease/cancel/budget/terminal states |
| `backend/app/models/derivative_generation.py` | `reader_chat.py`, `narrative_memory.py` | candidate/review/override lineage |
| `backend/tests/adversarial/test_derivative_generation_boundaries.py` | `test_reader_chat_boundaries.py`, `test_narrative_memory_retrieval_safety.py` | cutoff, evidence, no-write negative tests |

The provider gateway must be injectable/fakeable like existing model gateways; publication remains a deterministic service action.[CITED: repository analogs]
