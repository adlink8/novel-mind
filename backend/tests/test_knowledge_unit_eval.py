"""Candidate-bound frozen evaluation evidence tests."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.knowledge_units.eval import (
    NarrativeEvalError,
    evaluate_candidate,
    load_fixture,
    verify_run,
    candidate_retriever,
)
from app.services.knowledge_units.search import NarrativeRetrievalStrategy

EVALS = Path(__file__).parents[1] / "evals"


def _build(domain="fiction"):
    return SimpleNamespace(
        id=7,
        status="candidate",
        collection_name="candidate_7",
        manifest_checksum="a" * 64,
        owner_id=11,
        novel_id=22,
        domain_profile=domain,
    )


@pytest.mark.parametrize(
    "name,domain",
    [
        ("narrative_units_fiction.json", "fiction"),
        ("narrative_units_history.json", "history"),
    ],
)
async def test_eval_calls_retrieval_for_every_query_and_strategy(name, domain):
    payload = load_fixture(EVALS / name)
    calls = []

    async def retrieve(query, context):
        calls.append((query, context.copy()))
        case = next(case for case in payload["cases"] if case["query"] == query)
        return [
            {
                "id": case["gold_ids"][0],
                "evidence_ids": case["gold_evidence_ids"],
                "metadata": {
                    "build_id": 7,
                    "manifest_checksum": "a" * 64,
                    "owner_id": 11,
                    "novel_id": 22,
                    "lifecycle_status": "current",
                },
            }
        ]

    report = await evaluate_candidate(
        payload, build=_build(domain), retrieve=retrieve, signing_secret="secret"
    )
    assert report["passed"] and verify_run(report, "secret")
    assert len(calls) == len(payload["cases"]) * 3
    assert all(
        row["strategies"]["hybrid"]["latency_ms"] >= 0 for row in report["outputs"]
    )


def test_frozen_hash_and_prefilled_answers_are_rejected():
    payload = json.loads(
        (EVALS / "narrative_units_fiction.json").read_text(encoding="utf-8")
    )
    payload["cases"][0]["retrieved"] = {"hybrid": ["u1"]}
    with pytest.raises(NarrativeEvalError):
        load_fixture_payload(payload)


def load_fixture_payload(payload):
    from app.services.knowledge_units.eval import validate_fixture

    return validate_fixture(payload)


async def test_wrong_candidate_metadata_cannot_pass():
    payload = load_fixture(EVALS / "narrative_units_fiction.json")

    async def retrieve(query, context):
        return [
            {
                "id": "u1",
                "metadata": {
                    "build_id": 999,
                    "manifest_checksum": "x",
                    "owner_id": 999,
                    "novel_id": 22,
                },
            }
        ]

    report = await evaluate_candidate(
        payload, build=_build(), retrieve=retrieve, signing_secret="secret"
    )
    assert not report["passed"] and not report["canary"]["passed"]


async def test_candidate_adapter_uses_production_policy_with_candidate_selector():
    build = _build()

    class Chunks:
        async def search_novel(self, db, *, novel_id, query, top_k):
            return [
                {
                    "chunk_id": 10,
                    "score": 0.8,
                    "content_snippet": "raw citation",
                }
            ]

    class Units:
        async def search_units(self, db, **kwargs):
            selected = await kwargs["build_selector"](
                db,
                kwargs["owner_id"],
                kwargs["novel_id"],
                kwargs["domain_profile"],
            )
            assert selected.id == build.id
            return [
                {
                    "unit_id": 20,
                    "chunk_id": 11,
                    "score": 1.0,
                    "evidence_refs": ["ev-20"],
                    "lifecycle": "disputed",
                }
            ]

    class DB:
        async def get(self, model, key):
            return build if key == build.id else None

    policy = NarrativeRetrievalStrategy(chunks=Chunks(), units=Units())
    adapter = candidate_retriever(build, db=DB(), strategy=policy)
    context = {"strategy": "hybrid", "top_k": 5}
    rows = await adapter("query", context)

    assert [row["id"] for row in rows] == ["20", "10"]
    assert rows[0]["evidence_ids"] == ["ev-20"]
    assert rows[0]["metadata"]["lifecycle_status"] == "disputed"
    assert all(row["metadata"]["build_id"] == build.id for row in rows)
