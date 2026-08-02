# Phase 27 Patterns — Current-Code Analogs

| Planned concern | Current analog | Mapping |
|---|---|---|
| event/causal facts | timeline/reconcile.py, evidence.py | extraction → evidence gate → version |
| relationships | relationships/worker.py, gates.py, overrides.py | candidate/judgment/accepted + override |
| clue lifecycle | clues/lifecycle.py, worker.py | append-only state and lineage |
| strict claims | narrative_memory/contracts.py | typed claims and source refs |
| version/provenance | narrative_memory/provenance.py, manifests.py | owner/novel/version/checksum |
| cutoff query | timeline/query.py, relationships/query.py | visibility and intervals |
| evidence UI | existing clue/relationship panels | display, not authority |

Prefer new world_model modules that follow these boundaries; verify exact names before
implementation. [VERIFIED: repository grep]
