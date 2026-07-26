"""
Facet read-only projection contract (Plan 23-02).

Automates the governance red lines fixed by docs/adr/0001-layer-registry.md
("Facet: Timeline / Relationship / Clue 不是层" + §5.4) and
docs/adr/0002-narrative-unit-vs-narrative-memory.md §2, closing the static
side of these audit entries
(.planning/ARCHITECTURE-LAYERING-DATA-GOVERNANCE-AUDIT-2026-07-17.md):

- NM-GOV-005 — Facets are read-only projections over the S* structural axis;
  facet-derived output must never write back to the S0-S2 main structure
  (or to the S4-S6 narrative-memory domain) without evidence lineage, and
  then feed itself in the next round.
- V08-BUILD-05 — Reader Chat must never become a source of fact for the
  hierarchical memory / facet domains.
- NM-GOV-006 — Neo4j is an optional serving projection; the adapter boundary
  only reads accepted PostgreSQL facts and never performs a
  Neo4j -> PostgreSQL domain write.

Method: pure static AST scanning of source files. No database, no app
import, no service startup — runs well inside the 15s contract budget.

Detection criteria (designed for low false positives; verified against the
current tree before freezing — see per-test docstrings for exemptions):

1. "Write" to a protected main-structure ORM model means one of:
   a. constructor call ``ProtectedModel(...)`` (a session.add() write path
      always requires instantiation first);
   b. SQLAlchemy bulk statement ``update(ProtectedModel)`` /
      ``delete(ProtectedModel)`` / ``insert(ProtectedModel)``;
   c. attribute assignment whose base is the protected class itself
      (``Chapter.content = ...``) or a local variable that provably holds a
      protected instance (inferred from ``x: ProtectedModel`` annotations,
      ``x = ProtectedModel(...)`` or ``x = await db.get(ProtectedModel, ..)``);
   d. ``setattr(x, ...)`` where x is a protected class or inferred instance.
   Plain reads — ``select(Chapter)``, ``db.get(TextChunk, id)``,
   ``ChunkHierarchyNode.level == "evidence"`` — are explicitly allowed.

2. The protected model set is derived from the AST of the model modules that
   define the S* main structure (ADR-0001 §1 SSOT tables), so new models in
   those files are covered automatically:
   novel.py (S0: Novel/Chapter), text_chunk.py (S1 legacy raw chain),
   chunk_build.py (S1-S3: ChunkBuild/ChunkActivePointer/ChunkHierarchyNode),
   narrative_memory*.py (S4-S6 domain — also off-limits to facets).
   Character / knowledge (KG intake, Phase 04) tables are deliberately NOT
   protected here: relationships/timeline_kg_backfill.py legitimately writes
   the KG domain, which is not S* main structure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
MODELS = APP / "models"
SERVICES = APP / "services"

# ADR-0001 §1: model modules whose ORM classes form the S* main structure
# (plus the S4-S6 narrative-memory domain, protected from facet writes).
STRUCTURE_MODEL_FILES = (
    MODELS / "novel.py",
    MODELS / "text_chunk.py",
    MODELS / "chunk_build.py",
    MODELS / "narrative_memory.py",
    MODELS / "narrative_memory_builder.py",
    MODELS / "narrative_memory_qualification.py",
    MODELS / "narrative_memory_rebuild.py",
)

# Facet service packages (read-only projections per ADR-0001).
FACET_PACKAGES = (
    SERVICES / "timeline",
    SERVICES / "relationships",
    SERVICES / "clues",
)

# Packages that must never import reader_chat (ADR-0002 §2 / V08-BUILD-05).
NO_READER_CHAT_PACKAGES = FACET_PACKAGES + (SERVICES / "narrative_memory",)

# Neo4j projection boundary modules (NM-GOV-006). Grep-located: these are the
# only modules implementing the optional Neo4j projection today. There is no
# runtime neo4j driver code in the tree (no `import neo4j` anywhere); the
# adapters return `neo4j_driver_not_configured` until one is wired, so this
# contract binds the PostgreSQL-side boundary that would feed a driver.
NEO4J_BOUNDARY_MODULES = (
    SERVICES / "relationships" / "projection.py",
    SERVICES / "knowledge" / "graph_sync.py",
)

_BULK_WRITE_FUNCS = {"update", "delete", "insert", "sa_update", "sa_delete"}

# Precise exemption list for attribute writes found in the current tree,
# keyed by (path relative to app/services, model class, attribute).
#
# timeline/worker.py::_finish_run mirrors the analysis run outcome into the
# bookshelf workflow field `Novel.status` ("analyzing"/"analyzed"/"ready",
# comment: "书架状态与时间线任务对齐（Phase 08 产品面）"). This is a derived
# UI/workflow field hung on the S0 row — analogous to ADR-0001's note that
# `Chapter.summary` is "挂在 S0 行上的派生展示字段，不属于 S0 事实本身". It is
# not narrative fact, does not touch Chapter.content or structure tables, and
# is never consumed as facet input, so it does not close the NM-GOV-005
# feedback loop. It IS however a facet-package write into an S0 SSOT table.
# TODO(Phase 25 facet contract): adjudicate whether bookshelf workflow status
# should move out of the facet worker (e.g. into an application-layer novel
# service) so this exemption can be deleted. Do not widen this list without
# an ADR reference.
ALLOWED_ATTRIBUTE_WRITES = {
    ("timeline/worker.py", "Novel", "status"),
}


def _protected_model_names() -> frozenset[str]:
    """Class names defined at top level of the structure model modules."""
    names: set[str] = set()
    for path in STRUCTURE_MODEL_FILES:
        assert path.is_file(), f"structure model module missing: {path}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                names.add(node.name)
    return frozenset(names)


PROTECTED = _protected_model_names()


def _python_files(package: Path) -> list[Path]:
    files = sorted(package.rglob("*.py"))
    assert files, f"no python files found under {package} — scan is vacuous"
    return files


def _annotation_name(node: ast.expr | None) -> str | None:
    """Resolve `x: Chapter` / `x: models.Chapter` annotations to a name."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _call_target_name(func: ast.expr) -> str | None:
    """Name being called: `Chapter(...)` or `models.Chapter(...)`."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _infer_protected_vars(tree: ast.AST) -> dict[str, str]:
    """Variables that provably hold a protected model instance (var -> class).

    Sources of inference (module-wide over-approximation; a collision would
    surface as a reviewable failure, never as a silent pass):
    - function parameter / AnnAssign annotations: ``build: ChunkBuild``
    - direct construction: ``x = Chapter(...)``
    - session lookup: ``x = await db.get(Chapter, ...)`` / ``session.get``
    """
    inferred: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = node.args
            for arg in [
                *args.posonlyargs,
                *args.args,
                *args.kwonlyargs,
            ]:
                ann = _annotation_name(arg.annotation)
                if ann in PROTECTED:
                    inferred[arg.arg] = ann
        elif isinstance(node, ast.AnnAssign):
            ann = _annotation_name(node.annotation)
            if isinstance(node.target, ast.Name) and ann in PROTECTED:
                inferred[node.target.id] = ann
        elif isinstance(node, ast.Assign):
            value = node.value
            if isinstance(value, ast.Await):
                value = value.value
            name: str | None = None
            if isinstance(value, ast.Call):
                target = _call_target_name(value.func)
                if target in PROTECTED:
                    name = target
                elif (
                    isinstance(value.func, ast.Attribute)
                    and value.func.attr == "get"
                    and value.args
                    and isinstance(value.args[0], ast.Name)
                    and value.args[0].id in PROTECTED
                ):
                    name = value.args[0].id
            if name is not None:
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        inferred[tgt.id] = name
    return inferred


def _find_structure_writes(path: Path) -> list[str]:
    """Return human-readable violations of the read-only criteria in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    protected_vars = _infer_protected_vars(tree)
    violations: list[str] = []

    def flag(node: ast.AST, message: str) -> None:
        violations.append(f"{path}:{getattr(node, 'lineno', '?')}: {message}")

    def protected_class_of(node: ast.expr) -> str | None:
        if not isinstance(node, ast.Name):
            return None
        if node.id in PROTECTED:
            return node.id
        return protected_vars.get(node.id)

    def is_protected_expr(node: ast.expr) -> bool:
        return protected_class_of(node) is not None

    try:
        rel = path.relative_to(SERVICES).as_posix()
    except ValueError:
        rel = path.as_posix()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _call_target_name(node.func)
            if target in PROTECTED:
                flag(node, f"instantiates protected model {target}(...)")
            elif target in _BULK_WRITE_FUNCS:
                for arg in node.args:
                    name = (
                        arg.id
                        if isinstance(arg, ast.Name)
                        else arg.attr
                        if isinstance(arg, ast.Attribute)
                        else None
                    )
                    if name in PROTECTED:
                        flag(node, f"bulk {target}({name}) on protected model")
            elif target == "setattr" and node.args and is_protected_expr(node.args[0]):
                flag(node, "setattr on protected model instance")
        elif isinstance(node, ast.Assign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if not isinstance(tgt, ast.Attribute):
                    continue
                cls = protected_class_of(tgt.value)
                if cls is None:
                    continue
                if (rel, cls, tgt.attr) in ALLOWED_ATTRIBUTE_WRITES:
                    continue
                flag(
                    node,
                    f"attribute write on protected model: {cls}.{tgt.attr} = ...",
                )
    return violations


def _imports_of(path: Path) -> list[tuple[int, str]]:
    """All imported dotted paths in a file as (lineno, dotted_path)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                dotted = f"{module}.{alias.name}" if module else alias.name
                found.append((node.lineno, dotted))
    return found


def test_protected_model_set_is_not_vacuous():
    """Guard: AST-derived protected set must contain the ADR-0001 anchors.

    If a model file is moved/renamed, this fails loudly instead of letting
    the read-only assertions pass against an empty set (NM-GOV-005).
    """
    expected = {
        "Novel",
        "Chapter",
        "TextChunk",
        "ChunkBuild",
        "ChunkActivePointer",
        "ChunkHierarchyNode",
        "NarrativeMemoryVersion",
        "NarrativeMemoryNode",
        "NarrativeMemoryClaim",
        "NarrativeMemorySourceLink",
    }
    missing = expected - PROTECTED
    assert not missing, f"protected model set lost ADR anchors: {missing}"


def test_facet_services_do_not_write_structure_models():
    """NM-GOV-005 (ADR-0001 §1 Facet, §5.4): facets are read-only projections.

    Timeline / Relationship / Clue service packages may SELECT the S* main
    structure (Chapter, TextChunk, ChunkBuild, ChunkHierarchyNode, ...) and
    the narrative-memory domain, but must never instantiate, bulk-update,
    bulk-delete, or attribute-assign those models. Their only write paths
    live inside their own facet domains (timeline.py / relationship.py /
    clue.py models — candidate->accepted->active lifecycle).

    Known-legitimate reads kept green by design (verified 2026-07-27):
    - timeline/worker.py, clues/worker.py: select(ChunkBuild/
      ChunkHierarchyNode/Chapter) to locate evidence context;
    - relationships/candidates.py: db.get(TextChunk|Chapter, id) for
      evidence display (read-only, no attribute writes);
    - relationships/timeline_kg_backfill.py writes Character/KG-domain
      tables — Phase 04 KG intake domain, intentionally outside the
      protected set (not S* main structure).

    One exact attribute-write exemption exists (bookshelf workflow field
    `Novel.status` in timeline/worker.py) — see ALLOWED_ATTRIBUTE_WRITES
    for the rationale and the Phase 25 TODO.
    """
    violations: list[str] = []
    for package in FACET_PACKAGES:
        for path in _python_files(package):
            violations.extend(_find_structure_writes(path))
    assert not violations, (
        "Facet services must not write S* main-structure / narrative-memory "
        "models (NM-GOV-005):\n" + "\n".join(violations)
    )


def test_facet_and_memory_services_do_not_import_reader_chat():
    """V08-BUILD-05 (ADR-0002 §2): Reader Chat is never a facet fact source.

    narrative_memory, timeline, relationships and clues service packages
    must not import any reader_chat module (services, models or schemas).
    String literals mentioning "reader_chat" (e.g. narrative_memory builder
    contracts that blocklist it, or docstrings) are intentionally allowed —
    only real imports are violations, hence AST import scanning, not grep.
    """
    violations: list[str] = []
    for package in NO_READER_CHAT_PACKAGES:
        for path in _python_files(package):
            for lineno, dotted in _imports_of(path):
                if "reader_chat" in dotted.split("."):
                    violations.append(f"{path}:{lineno}: imports {dotted}")
    assert not violations, (
        "Facet/memory services must not import reader_chat "
        "(V08-BUILD-05):\n" + "\n".join(violations)
    )


def test_neo4j_projection_boundary_is_read_only():
    """NM-GOV-006 (ADR-0001 §2 D*-Serving): Neo4j is a disposable projection.

    The Neo4j boundary modules (relationships/projection.py "Replayable
    Neo4j/projection boundary", knowledge/graph_sync.py "Optional Neo4j sync
    boundary") read accepted PostgreSQL facts and may write only their own
    non-authoritative audit/checkpoint rows (RelationshipProjectionAudit).
    They must not reference the S* main-structure / narrative-memory models
    at all — neither imports nor writes — so no Neo4j -> PostgreSQL domain
    write path can exist.

    There is currently no runtime neo4j driver in the repo (no `import
    neo4j`); adapters fail closed with `neo4j_driver_not_configured`. If a
    real driver adapter is added later, add its module to
    NEO4J_BOUNDARY_MODULES so it inherits this contract.
    """
    protected_modules = {f"app.models.{path.stem}" for path in STRUCTURE_MODEL_FILES}
    violations: list[str] = []
    for path in NEO4J_BOUNDARY_MODULES:
        assert path.is_file(), (
            f"Neo4j boundary module moved: {path} — update "
            "NEO4J_BOUNDARY_MODULES to keep NM-GOV-006 enforced"
        )
        violations.extend(_find_structure_writes(path))
        for lineno, dotted in _imports_of(path):
            head = dotted.rsplit(".", 1)[0]
            leaf = dotted.rsplit(".", 1)[-1]
            if head in protected_modules or leaf in PROTECTED:
                violations.append(
                    f"{path}:{lineno}: imports protected structure model path {dotted}"
                )
    assert not violations, (
        "Neo4j projection boundary must stay read-only over the main "
        "structure (NM-GOV-006):\n" + "\n".join(violations)
    )
