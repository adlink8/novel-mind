"""Generate the backend builtin Skill snapshot from agent-service authority.

The agent-service ``skill.yaml``, JSON schemas and ``SKILL.md`` are the single
authoritative contract source.  Backend runtime code consumes only the checked-in
JSON snapshot produced here; it never reads client/service source files at runtime.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "agent-service" / "src" / "skills"
LOADER_PATH = SOURCE_ROOT / "loader.ts"
OUTPUT_PATH = (
    REPO_ROOT
    / "backend"
    / "app"
    / "services"
    / "agent_runtime"
    / "builtin_manifests.json"
)


def _checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _allowlist() -> list[str]:
    source = LOADER_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"export const ALLOWLISTED_SKILL_DIRS = \[(.*?)\] as const;",
        source,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("agent-service loader allowlist not found")
    return re.findall(r'"([a-z0-9]+(?:-[a-z0-9]+)*)"', match.group(1))


def build_snapshot() -> dict[str, Any]:
    names = _allowlist()
    skills: list[dict[str, Any]] = []
    for name in names:
        skill_dir = SOURCE_ROOT / name
        yaml_path = skill_dir / "skill.yaml"
        instructions_path = skill_dir / "SKILL.md"
        if not yaml_path.is_file() or not instructions_path.is_file():
            raise RuntimeError(f"builtin Skill contract is incomplete: {name}")

        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("name") != name:
            raise RuntimeError(f"invalid builtin skill.yaml name: {name}")

        input_schema_path = skill_dir / str(raw["input_schema"])
        output_schema_path = skill_dir / str(raw["output_schema"])
        input_schema = json.loads(input_schema_path.read_text(encoding="utf-8"))
        output_schema = json.loads(output_schema_path.read_text(encoding="utf-8"))
        prompt = instructions_path.read_text(encoding="utf-8")
        payload = {
            "name": name,
            "version": str(raw["version"]),
            "description": str(raw.get("description") or ""),
            "prompt": prompt,
            "execution_mode": "builtin",
            "allowed_tools": list(raw["allowed_tools"]),
            "read_permissions": list(raw.get("read_permissions") or []),
            "write_permissions": list(raw.get("write_permissions") or []),
            "forbidden_spaces": list(raw.get("forbidden_spaces") or []),
            "budget": dict(raw.get("budget") or {}),
            "approval_required_for": list(raw.get("approval_required_for") or []),
            "input_schema": input_schema,
            "output_schema": output_schema,
        }
        source_digest = hashlib.sha256(
            b"".join(
                path.read_bytes()
                for path in (yaml_path, input_schema_path, output_schema_path, instructions_path)
            )
        ).hexdigest()
        skills.append({**payload, "checksum": _checksum(payload), "source_checksum": source_digest})

    if {item["name"] for item in skills} != set(names):
        raise RuntimeError("builtin Skill allowlist contains duplicate or missing entries")
    return {
        "schema_version": "builtin-skill-manifest.v1",
        "source": "agent-service/src/skills",
        "allowlisted_skill_names": names,
        "skills": skills,
    }


def main() -> None:
    snapshot = build_snapshot()
    OUTPUT_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(snapshot['skills'])} builtin Skill manifests to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
