#!/usr/bin/env python3
"""
Deterministically export the FastAPI OpenAPI schema.

Usage:
  python scripts/export_openapi.py --output artifacts/openapi.json
  python scripts/export_openapi.py --output openapi-baseline.json

Export is stable: sorted keys, indent=2, trailing newline, no host/server noise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def export_schema() -> dict:
    """Import the FastAPI app and return a cleaned OpenAPI 3 schema dict."""
    backend = _repo_backend_root()
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))

    from app.main import app  # noqa: WPS433 — runtime import after path setup

    schema = app.openapi()
    # Stable metadata only; drop runtime-variable fields if present.
    schema.pop("x-generator", None)
    return schema


def write_schema(schema: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)
    if not text.endswith("\n"):
        text += "\n"
    output.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export FastAPI OpenAPI schema")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="Destination JSON path (relative to CWD or absolute)",
    )
    args = parser.parse_args(argv)

    schema = export_schema()
    write_schema(schema, args.output)
    print(f"Wrote OpenAPI schema -> {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
