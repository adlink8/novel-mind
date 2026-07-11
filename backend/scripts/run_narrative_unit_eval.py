"""Run deterministic frozen narrative retrieval evaluation."""

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.knowledge_units.eval import load_and_evaluate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--latency-budget-ms", type=float, default=1000.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = load_and_evaluate(args.fixture, latency_budget_ms=args.latency_budget_ms)
    report["dry_run"] = args.dry_run
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
