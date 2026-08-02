# Phase 26 Patterns — Current-Code Analogs

| Planned file | Current analog | Preserve |
|---|---|---|
| queryplan/schemas.py | reader_chat schemas; narrative_memory contracts | strict enums and lineage |
| queryplan/parser.py | narrative_memory/routing.py | deterministic policy/hash; clarification |
| queryplan/adapters.py | timeline/query.py, relationships/query.py, clues/query.py, candidate_reader.py | owner/cutoff/status |
| queryplan/fusion.py | knowledge_units/search.py fuse_results | deterministic baseline |
| queryplan/evidence.py | narrative_memory/citations.py/source_snapshot.py | leaf/hash rejection |
| queryplan/service.py | reader_chat/context.py/conversations.py | trace and freeze |
| Reader consumer | reader_chat selection anchor | shared core |
| Analysis consumer | analysis-chat-panel.tsx and ChapterRange | range anchor |
| Fixtures | single_book_v1.json and reader-chat adversarial tests | frozen corpus/negative cases |

Do not make NarrativeRetrievalStrategy a question router or read narrative-memory claim
tables directly; those violate current layer/candidate boundaries. [VERIFIED: repository grep]
