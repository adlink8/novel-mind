#!/usr/bin/env python3
"""
OpenAPI breaking-change detector (oasdiff-compatible fallback).

When the `oasdiff` binary is available it is preferred (locked v1.17.0).
Otherwise this pure-Python checker enforces the same high-severity categories
required by 06-05:
  - path delete
  - response/request schema type change
  - required property added
  - auth/security requirement change
  - response status code removed/changed

Exit codes:
  0 — no breaking changes (or only non-breaking diffs)
  1 — breaking changes found
  2 — tool/IO error
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


Breaking = list[str]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _paths(doc: dict[str, Any]) -> dict[str, Any]:
    return doc.get("paths") or {}


def _ops(path_item: dict[str, Any]) -> dict[str, Any]:
    methods = ("get", "post", "put", "patch", "delete", "options", "head", "trace")
    return {m: path_item[m] for m in methods if m in path_item and isinstance(path_item[m], dict)}


def _schema_type(schema: dict[str, Any] | None) -> str | None:
    if not schema or not isinstance(schema, dict):
        return None
    if "type" in schema:
        return str(schema["type"])
    if "$ref" in schema:
        return f"$ref:{schema['$ref']}"
    if "allOf" in schema:
        return "allOf"
    if "oneOf" in schema:
        return "oneOf"
    if "anyOf" in schema:
        return "anyOf"
    return None


def _collect_required(schema: dict[str, Any] | None) -> set[str]:
    if not schema or not isinstance(schema, dict):
        return set()
    required: set[str] = set()
    if isinstance(schema.get("required"), list):
        required.update(str(x) for x in schema["required"])
    props = schema.get("properties") or {}
    if isinstance(props, dict):
        for name, sub in props.items():
            # Nested object required only counted at this level for simplicity.
            _ = name, sub
    return required


def _request_schema(op: dict[str, Any]) -> dict[str, Any] | None:
    rb = op.get("requestBody") or {}
    content = rb.get("content") or {}
    for media in content.values():
        if isinstance(media, dict) and "schema" in media:
            return media["schema"]
    return None


def _response_schema(op: dict[str, Any], code: str) -> dict[str, Any] | None:
    responses = op.get("responses") or {}
    resp = responses.get(code) or {}
    content = resp.get("content") or {}
    for media in content.values():
        if isinstance(media, dict) and "schema" in media:
            return media["schema"]
    return None


def _security(op: dict[str, Any], doc: dict[str, Any]) -> list[Any]:
    if "security" in op:
        return list(op["security"] or [])
    if "security" in doc:
        return list(doc["security"] or [])
    return []


def detect_breaking(base: dict[str, Any], rev: dict[str, Any]) -> Breaking:
    """Return human-readable breaking change messages (empty = non-breaking)."""
    findings: Breaking = []
    base_paths = _paths(base)
    rev_paths = _paths(rev)

    for path in sorted(base_paths.keys()):
        if path not in rev_paths:
            findings.append(f"path-deleted: {path}")
            continue

        base_ops = _ops(base_paths[path])
        rev_ops = _ops(rev_paths[path])
        for method, b_op in base_ops.items():
            if method not in rev_ops:
                findings.append(f"operation-deleted: {method.upper()} {path}")
                continue
            r_op = rev_ops[method]

            # Status codes: removing a documented success/error code is breaking
            b_codes = set((b_op.get("responses") or {}).keys())
            r_codes = set((r_op.get("responses") or {}).keys())
            for code in sorted(b_codes - r_codes):
                findings.append(f"status-code-removed: {method.upper()} {path} {code}")

            # Auth / security requirements
            if _security(b_op, base) != _security(r_op, rev):
                findings.append(f"auth-changed: {method.upper()} {path}")

            # Request body type change
            b_req = _request_schema(b_op)
            r_req = _request_schema(r_op)
            b_type = _schema_type(b_req)
            r_type = _schema_type(r_req)
            if b_type and r_type and b_type != r_type:
                findings.append(
                    f"request-type-changed: {method.upper()} {path} {b_type}->{r_type}"
                )

            # Required properties added on request schema (breaking for clients)
            b_req_props = _collect_required(b_req)
            r_req_props = _collect_required(r_req)
            added = r_req_props - b_req_props
            for name in sorted(added):
                findings.append(
                    f"required-added: {method.upper()} {path} field={name}"
                )

            # Response body type change for shared status codes
            for code in sorted(b_codes & r_codes):
                b_rs = _response_schema(b_op, code)
                r_rs = _response_schema(r_op, code)
                bt = _schema_type(b_rs)
                rt = _schema_type(r_rs)
                if bt and rt and bt != rt:
                    findings.append(
                        f"response-type-changed: {method.upper()} {path} "
                        f"{code} {bt}->{rt}"
                    )

    return findings


def run_oasdiff(base: Path, rev: Path) -> tuple[int, str]:
    """Run oasdiff breaking; return (exit_code, combined_output)."""
    binary = shutil.which("oasdiff")
    if not binary:
        return 127, "oasdiff not found on PATH"
    proc = subprocess.run(
        [binary, "breaking", str(base), str(rev), "--fail-on", "ERR"],
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI breaking check")
    parser.add_argument("base", type=Path, help="Baseline OpenAPI JSON")
    parser.add_argument("revision", type=Path, help="Candidate OpenAPI JSON")
    parser.add_argument(
        "--prefer-oasdiff",
        action="store_true",
        default=True,
        help="Prefer oasdiff binary when available (default)",
    )
    parser.add_argument(
        "--python-only",
        action="store_true",
        help="Force pure-Python checker",
    )
    args = parser.parse_args(argv)

    if not args.base.is_file() or not args.revision.is_file():
        print("error: base/revision file missing", file=sys.stderr)
        return 2

    if not args.python_only and args.prefer_oasdiff and shutil.which("oasdiff"):
        code, out = run_oasdiff(args.base, args.revision)
        if out.strip():
            print(out.strip())
        return 0 if code == 0 else 1

    base = _load(args.base)
    rev = _load(args.revision)
    findings = detect_breaking(base, rev)
    if findings:
        print("BREAKING CHANGES:")
        for f in findings:
            print(f"  - {f}")
        return 1
    print("No breaking changes detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
