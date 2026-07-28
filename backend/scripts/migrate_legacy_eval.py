#!/usr/bin/env python3
"""Migrate legacy gold_chunks DB IDs to content-hash evidence (06-04).

Provable mappings (id -> content_hash) become migration candidates.
Unprovable entries are quarantined and never quality_comparable.

Examples:
  python scripts/migrate_legacy_eval.py --input evals/novel_eval_candidates.json \\
      --mapping evals/chunk_id_to_hash.json --output evals/results/migration.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.eval_service import EvalService


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy eval gold_chunks")
    parser.add_argument("--input", required=True, help="Legacy candidates JSON list")
    parser.add_argument(
        "--mapping",
        default=None,
        help="JSON object mapping chunk_id (str or int keys) -> content_hash",
    )
    parser.add_argument(
        "--output",
        default="evals/results/legacy_migration.json",
        help="Migration report path",
    )
    args = parser.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        print("[ERR] input must be a JSON list of candidates", file=sys.stderr)
        return 2

    id_to_hash: dict[int, str] = {}
    if args.mapping:
        m = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
        for k, v in m.items():
            id_to_hash[int(k)] = str(v)

    svc = EvalService()
    migrated = []
    quarantined = []
    for item in raw:
        gold = item.get("gold_chunks") or item.get("gold") or []
        classification = svc.classify_legacy_gold(gold, id_to_hash=id_to_hash or None)
        row = {
            "question": item.get("question"),
            "novel_id": item.get("novel_id"),
            "gold_chunks": gold,
            **classification,
        }
        if classification["status"] == "migrated":
            migrated.append(row)
        else:
            quarantined.append(row)

    report = {
        "input": str(args.input),
        "mapping_provided": bool(args.mapping),
        "total": len(raw),
        "migrated_count": len(migrated),
        "quarantined_count": len(quarantined),
        "migrated": migrated,
        "quarantined": quarantined,
        "note": (
            "Migrated hashes still require full fixture freeze + calibration "
            "before quality_comparable qualification (06-03/06-04)."
        ),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[OK] total={report['total']} migrated={report['migrated_count']} "
        f"quarantined={report['quarantined_count']} -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
