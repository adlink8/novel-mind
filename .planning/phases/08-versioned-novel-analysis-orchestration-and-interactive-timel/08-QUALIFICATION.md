# Phase 08 Qualification

**Release status: QUALIFIED**

Production worker and persisted PostgreSQL/SQLAlchemy artifacts are the qualification authority.

- Run: `10` / version `22` / `completed`
- Artifact SHA-256: `ddd1f839eced73e557f7156aeaf6ede08639383197c392fe6aeb22849e02734e`
- Report SHA-256: `81c216866fdbb1c8442d2047165c2c67e0bfac3e997bdfdf69bdf5f729c45345`

## Measured Production Artifacts

| Artifact | Count |
|---|---:|
| Persisted events | 2 |
| Evidence refs | 2 |
| Model attempts | 3 |
| Completed stages | 3 |

## Measured Metrics

| Metric | Value |
|---|---:|
| Event precision | 1.000 |
| Event recall | 1.000 |
| Story pairwise accuracy | 1.000 |
| Evidence coverage | 1.000 |
| Spoiler leaks | 0 |
| Provider calls | 3 |
| Settled cost | $0.00012000 |
| p95 latency | 0.0 ms |

## Gates

- PASS — `worker_completed`
- PASS — `active_promoted`
- PASS — `production_artifacts`
- PASS — `call_audit`
- PASS — `budget_settled`
- PASS — `spoiler_safety`
- PASS — `quality_thresholds`

## Required Test Commands

- `cd backend; pytest tests/unit/timeline tests/integration/timeline tests/adversarial/test_timeline_evidence.py -x`
- `cd frontend; npm test -- --run`
- `cd frontend; npm run build`
- `cd frontend; npm run test:e2e -- timeline-real.spec.ts`
- `pytest tests/ci/test_timeline_release_gate.py -x`

## Signed Raw Artifact

The canonical JSON below hashes to the artifact SHA-256 above.

```json
{
  "active_pointer": {
    "manifest_checksum": "2945803ac0ca10e9f88862f94beccf92c908d8e0ac8dc7110f8858afa9f1b462",
    "revision": 1,
    "version_id": 22
  },
  "attempts": [
    {
      "attempt_number": 1,
      "cost_usd": "0.00004000",
      "id": 20,
      "latency_ms": 0,
      "provider_request_id": "qualification-request-1",
      "request_hash": "877d50957584f902ae8cc8e7ada532c722c3971a2849e4c2c69a829364aee677",
      "response_hash": "977b786dced0d707a93e62761b47d3ffe6d075fb1030aa97ff5668ccf60dd641",
      "stage_key": "chapter_extract:13",
      "status": "succeeded",
      "usage": {
        "input_tokens": 20,
        "output_tokens": 10
      }
    },
    {
      "attempt_number": 1,
      "cost_usd": "0.00004000",
      "id": 21,
      "latency_ms": 0,
      "provider_request_id": "qualification-request-2",
      "request_hash": "a0f48cd4726397eb878edd6b1157eb3a2aa71bbcaf3ab68604f6f205167f223e",
      "response_hash": "4c08a2e5aabc66f1cc7bf101e848c93d4899d11d064aa8acb9fb4ba0e25b3bc8",
      "stage_key": "chapter_extract:14",
      "status": "succeeded",
      "usage": {
        "input_tokens": 20,
        "output_tokens": 10
      }
    },
    {
      "attempt_number": 1,
      "cost_usd": "0.00004000",
      "id": 22,
      "latency_ms": 0,
      "provider_request_id": "qualification-request-3",
      "request_hash": "11bf01079022154f0a95658d9939832cde6971b3deb7de7e7889c7b755f0ee6f",
      "response_hash": "5f9b0d30ba7fa0cd3e5ad7671b45c8d96d5fa215dd24a5613b0d626fe5a94d03",
      "stage_key": "cross_chapter_reconcile:book",
      "status": "succeeded",
      "usage": {
        "input_tokens": 20,
        "output_tokens": 10
      }
    }
  ],
  "budget": {
    "reserved_calls": 0,
    "settled_calls": 3,
    "settled_cost_usd": "0.00012000",
    "settled_input_tokens": 60,
    "settled_output_tokens": 30
  },
  "counts": {
    "completed_stages": 3,
    "events": 2,
    "evidence_refs": 2,
    "model_attempts": 3
  },
  "database_dialect": "postgresql",
  "events": [
    {
      "chapter_number": 2,
      "id": 25,
      "logical_event_id": "event-13",
      "narrative_index": 0,
      "publication_status": "provisional",
      "story_rank": 0
    },
    {
      "chapter_number": 9,
      "id": 26,
      "logical_event_id": "event-14",
      "narrative_index": 0,
      "publication_status": "provisional",
      "story_rank": 1
    }
  ],
  "evidence_refs": [
    {
      "content_hash": "7780ba0dbb3a2c667094069e50512d40a9cdaa9a92c0e96cd874de245b31ceb1",
      "event_id": 25,
      "evidence_id": "qualification-evidence-13",
      "source_end": 11,
      "source_start": 0
    },
    {
      "content_hash": "7746135111fb9b32b150cfa044816202aa71d3d8612cc1e933924d44e65da0fd",
      "event_id": 26,
      "evidence_id": "qualification-evidence-14",
      "source_end": 12,
      "source_start": 0
    }
  ],
  "run": {
    "id": 10,
    "progress": {
      "completed_chapters": 2,
      "stage": "completed",
      "total_chapters": 2
    },
    "status": "completed",
    "version_id": 22
  },
  "stages": [
    {
      "artifact_checksum": "a6b67f0d0c1d6a912fd4f98b9af12a9940e68f555cca3c08aa3eb49f43b448ef",
      "stage_key": "chapter_extract:13"
    },
    {
      "artifact_checksum": "e8f002b3c9f372b3bf823fcd0726660d3cc2be4402844a1bc57727144f11ad94",
      "stage_key": "chapter_extract:14"
    },
    {
      "artifact_checksum": "a271e7106c6654f94d3995da9ef93ce1d5c1afdfcfeb65ffdca16a2a5598b5ba",
      "stage_key": "cross_chapter_reconcile:book"
    }
  ],
  "version": {
    "hierarchy_build_id": "qualification-481cabb8810f4a89958e47935cfb2403",
    "hierarchy_checksum": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "id": 22,
    "manifest_checksum": "2945803ac0ca10e9f88862f94beccf92c908d8e0ac8dc7110f8858afa9f1b462",
    "model_lineage": {
      "chapter_extract": [
        "controlled",
        "balanced-qualified",
        "r1"
      ],
      "cross_chapter_reconcile": [
        "controlled",
        "quality-qualified",
        "r1"
      ]
    },
    "prompt_hash": "3f57f586f4c66ae86f390a6603dcdb86d464694c33a0855cbc6dd186e92716df",
    "schema_hash": "e65e1eb07365fc47506c629127c2b7db5a40875924a07fbc02072ef001b8a79f",
    "source_snapshot_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "status": "active"
  },
  "visible_default_event_ids": [
    "event-13"
  ]
}
```
