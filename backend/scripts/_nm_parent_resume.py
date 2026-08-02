#!/usr/bin/env python3
"""Bounded candidate-only resume for Arc/Global/manifest stages."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts._nm_resume_loop import one_batch, status_snapshot


async def _main(args: argparse.Namespace) -> int:
    result = await one_batch(args.owner_id, args.novel_id, args.version_id, args.max_stages)
    snapshot = await status_snapshot(args.owner_id, args.novel_id, args.version_id)
    print(json.dumps({"result": result, "snapshot": snapshot}, ensure_ascii=False))
    return 0 if result.get("status") == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="_nm_parent_resume")
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--novel-id", type=int, required=True)
    parser.add_argument("--version-id", type=int, required=True)
    parser.add_argument("--max-stages", type=int, default=20)
    args = parser.parse_args()
    if args.max_stages < 1:
        parser.error("--max-stages must be positive")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
