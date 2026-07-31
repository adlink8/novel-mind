from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.analysis_orchestrator import run_full_analysis


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", type=int)
    args = parser.parse_args()
    asyncio.run(run_full_analysis(args.run_id))


if __name__ == "__main__":
    main()
